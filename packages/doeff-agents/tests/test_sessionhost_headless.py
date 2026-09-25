"""sessionhost の headless backend(agora-redesign #37・段 2 lane 2d)の焦点の検。

3 層で撃つ:
1. 純粋な作法(headless_protocol): claude の stream-json の行 → 手番の終わり / codex の JSON-RPC
   の handshake → turn/start・turn/completed・断った server 要求 → 失敗 + turn/interrupt /
   turn_verdict の閉語彙。
2. 器(headless_process): 替え玉の claude / codex(tests/headless_stubs — API を撃たない)を
   実 process として起こし、events file・観測・割り込み・kill を実射。
3. host の RPC(dispatch-line・backend=headless・tmpdir の store): launch → monitor で
   turn_ended_at → send(claude は --resume の起こし直し・codex は温かい process)→ interrupt
   → cleanup。tmux は 1 度も触らない。
加えて `claude -p --help` の smoke(実 binary が在る機体だけ・API は撃たない)。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

import hy  # noqa: F401  # registers the .hy importer
import pytest
from doeff_agents.sessionhost import headless as headless_hy
from doeff_agents.sessionhost import headless_process, host
from doeff_agents.sessionhost.attachment import TurnAttachment, TurnContent
from doeff_agents.sessionhost.headless_process import (
    EOF_GRACE_SECONDS,
    TERM_GRACE_SECONDS,
    HeadlessProcess,
    HeadlessProcessStillAliveError,
    HeadlessRegistry,
)
from doeff_agents.sessionhost.headless_outbox import OutboxEventStore
from doeff_agents.sessionhost.headless_protocol import (
    JSON,
    BackendLiveness,
    ClaudeDialogue,
    CodexDialogue,
    CodexPlan,
    HeadlessObservation,
    JSONObject,
    TurnEnded,
    backend_alive,
    claude_interrupt_request_line,
    claude_user_line,
    parse_record,
    recovery_verdict,
    stop_cause_category,
    stop_verdict,
    turn_verdict,
)
from doeff_agents.sessionhost.impls import claude_code, fast_jev, headless_argv, otel_telemetry
from doeff_agents.sessionhost.store import StoreActor, terminal_cause_from_dict
from sessionhost_bin import resolve_sessionhost_bin
from sessionhost_isolated_host import isolated_host, sessionhost_serve_argv

from doeff import run


def _build_claude_headless(params: dict) -> dict:
    """headless の argv の組み立ては defk(ADR-DOE-HY-004 R3)— program を値にしてから読む。"""
    return run(headless_argv.build_claude_headless(params))


def _build_codex_headless(params: dict) -> dict:
    """同上(codex 側)。"""
    return run(headless_argv.build_codex_headless(params))


STUBS = Path(__file__).parent / "headless_stubs"


# ---------------------------------------------------------------- JSON の読み(検の narrowing の小片)


def _content(text: str, attachments: tuple[TurnAttachment, ...] = ()) -> TurnContent:
    """Dialogue が受ける手番 1 回分の入力(段 10 lane 10o: 本文 + 添付の型つきの値)。"""
    return TurnContent(text=text, attachments=attachments)


def _obj(value: JSON, key: str) -> JSONObject:
    assert isinstance(value, dict), value
    inner = value[key]
    assert isinstance(inner, dict), (key, inner)
    return inner


def _text(value: JSON, key: str) -> str:
    assert isinstance(value, dict), value
    inner = value[key]
    assert isinstance(inner, str), (key, inner)
    return inner


def _texts(value: JSON, key: str) -> list[str]:
    assert isinstance(value, dict), value
    inner = value[key]
    assert isinstance(inner, list), (key, inner)
    return [item for item in inner if isinstance(item, str)]


def _has(value: JSON, key: str) -> bool:
    assert isinstance(value, dict), value
    return key in value and value[key] is not None


def _record(line: str) -> JSONObject:
    parsed = parse_record(line)
    assert parsed is not None, line
    return parsed


def _pause(seconds: float) -> None:
    """実 process の検の有界の待ち(拍は実時間 — 替え玉の process が stdout に書くのを待つ)。"""
    threading.Event().wait(seconds)


# ---------------------------------------------------------------- 1. 純粋な作法


def test_claude_dialogue_reads_init_and_result() -> None:
    dialogue = ClaudeDialogue()
    assert dialogue.opening() == ()
    assert dialogue.one_process_per_turn is True  # 段 12 lane 12e(#517): 1 手番 1 process(温かい process は退役)
    # 手番が走る前の割り込みは引き受けない(新しい手番を起こさない)
    assert dialogue.inject(_content("early")).accepted is False
    turn = dialogue.turn(_content("hello"))
    assert turn.sends == (claude_user_line("hello"),)
    assert _record(turn.sends[0]) == {"type": "user", "message": {"role": "user", "content": "hello"}}
    assert turn.close_stdin is False
    assert dialogue.in_flight is True
    init = dialogue.on_line(_init())
    assert init.conversation == {"session_id": "sid-1"}
    assert dialogue.on_line({"type": "assistant", "message": {}}).ended is None
    # 走っている手番への割り込み = 同じ user の行(CLI が次の tool の境界で注入する)。行の uuid は
    # 呼び手の ref(無ければ鋳造)— CLI の command_lifecycle がこの綴りで運命を名乗る(段 10 lane 10n)。
    injected = dialogue.inject(_content("stop that"))
    assert injected.accepted is True
    assert injected.ref
    assert injected.sends == (claude_user_line("stop that", injected.ref),)
    assert _record(injected.sends[0])["uuid"] == injected.ref
    assert dialogue.injections == {injected.ref: "queued"}
    # 境界で畳まれた(started → completed が result の前)→ result は手番の終わり
    assert dialogue.on_line({"type": "command_lifecycle", "command_uuid": injected.ref, "state": "started"}).ended is None
    assert dialogue.injections == {injected.ref: "started"}
    assert dialogue.on_line({"type": "command_lifecycle", "command_uuid": injected.ref, "state": "completed"}).ended is None
    ended = dialogue.on_line({"type": "result", "subtype": "success", "is_error": False})
    assert ended.ended == TurnEnded(ok=True, detail="success")
    # 段 12 lane 12e(#517): 手番の終わり = 対話の終わり — 器はこの行で stdin に EOF を出して process を降ろす
    assert ended.close is True
    assert dialogue.on_line({"type": "assistant", "message": {}}).close is False
    assert dialogue.in_flight is False
    assert dialogue.injections == {}
    assert dialogue.inject(_content("late")).accepted is False
    # 手番の本文は stdin を閉じない(手番の途中の注入のため)— 閉じるのは result の行の器
    assert dialogue.turn(_content("again")).close_stdin is False
    failed = dialogue.on_line({"type": "result", "subtype": "error_max_turns", "is_error": True})
    assert failed.ended == TurnEnded(ok=False, detail="error_max_turns")
    assert failed.close is True
    assert dialogue.interrupt().signal is True
    # agora-redesign #513: API の誤りで終わった手番は CLI が構造で名乗った status を運ぶ
    # (実物 2.1.27x の形 — subtype success・is_error・terminal_reason api_error・api_error_status)
    assert dialogue.turn(_content("once more")).close_stdin is False
    refused = dialogue.on_line(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "terminal_reason": "api_error",
            "api_error_status": 429,
            "result": "Your group's usage limit is set to $0 · ask your admin for a higher limit",
        }
    )
    assert refused.ended == TurnEnded(
        ok=False,
        detail="Your group's usage limit is set to $0 · ask your admin for a higher limit",
        api_error_status=429,
    )
    # status は整数ちょうど(bool・文字列は status ではない)・成功の終わりは運ばない
    assert dialogue.turn(_content("and again")).close_stdin is False
    odd = dialogue.on_line(
        {"type": "result", "subtype": "success", "is_error": True, "api_error_status": "429", "result": "x"}
    )
    assert odd.ended == TurnEnded(ok=False, detail="x", api_error_status=None)
    assert dialogue.turn(_content("fine")).close_stdin is False
    fine = dialogue.on_line(
        {"type": "result", "subtype": "success", "is_error": False, "api_error_status": 429}
    )
    assert fine.ended == TurnEnded(ok=True, detail="success", api_error_status=None)


def _cli_own_result() -> JSONObject:
    """実物 2.1.263 の CLI 自身の手番の result(実測 2026-09-19 07:28 JST・pod j4bz4 の events)。"""
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "num_turns": 0,
        "duration_api_ms": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "modelUsage": {},
        "origin": {"kind": "task-notification"},
        "queued_turn_count": 0,
    }


def test_claude_dialogue_reads_past_the_cli_own_turn_to_the_prompts_result() -> None:
    """依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(実弾 aj-9AHT1RWPYNTTWEZBWRNN0R34T6): ``--resume`` で起きた CLI は本文より先に
    孤児の task の報せを自分の手番として走らせ、origin task-notification の result を返す。それは本文の手番の
    終わりではない — 手番は閉じず(close しない = process を降ろさない)、続く本文の手番の result で終わる。
    旧形はこの result で閉じて本文の手番を切り、出力 0 件の手番が completed を名乗った(郵便が黙って消費された)。"""
    dialogue = ClaudeDialogue()
    dialogue.turn(_content("the mail"))
    assert dialogue.on_line(_init()).ended is None
    own = dialogue.on_line(_cli_own_result())
    assert own.ended is None
    assert own.close is False
    assert dialogue.in_flight is True
    assert dialogue.cli_turn_open is True
    # 本文の手番: 2 つ目の init → 応答 → origin の無い result = 手番の終わり
    assert dialogue.on_line(_init()).ended is None
    assert dialogue.on_line({"type": "assistant", "message": {}}).ended is None
    ended = dialogue.on_line({"type": "result", "subtype": "success", "is_error": False})
    assert ended.ended == TurnEnded(ok=True, detail="success")
    assert ended.close is True
    # 手番の外に来た CLI 自身の result も手番の終わりを作らない(誰の手番でもない)
    assert dialogue.on_line(_cli_own_result()).ended is None
    # 閉語彙の外の origin(未知の種類)は今日どおり手番の終わり(発明しない — 読み流すのは名乗った種類だけ)
    dialogue.turn(_content("next"))
    unknown = dialogue.on_line(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "origin": {"kind": "something-else"},
        }
    )
    assert unknown.ended == TurnEnded(ok=True, detail="success")


def test_claude_dialogue_cli_own_result_does_not_answer_the_stop_signal() -> None:
    """停止の合図を出して答えを待つ間に CLI 自身の result が来ても、合図は消費されない(本文の手番の result を待つ)。"""
    dialogue = ClaudeDialogue()
    dialogue.turn(_content("run a long tool"))
    dialogue.on_line(_init())
    injected = dialogue.inject(_content("stop"))
    escalation = dialogue.escalate()
    assert escalation.accepted is True
    assert dialogue.on_line(_cli_own_result()).ended is None
    assert dialogue.escalation == escalation.request_id
    assert dialogue.injections == {injected.ref: "queued"}


def _lifecycle(ref: str, state: str) -> JSONObject:
    return {"type": "command_lifecycle", "command_uuid": ref, "state": state}


def _init(session_id: str = "sid-1", lifecycle: bool = True) -> JSONObject:
    """実物 2.1.270 の system/init(capabilities に msg_lifecycle_v1)— lifecycle=False は旧い CLI(名乗らない)。"""
    capabilities: list[JSON] = (
        ["interrupt_receipt_v1", "interrupt_cancel_queued_v1", "msg_lifecycle_v1"] if lifecycle else []
    )
    return {"type": "system", "subtype": "init", "session_id": session_id, "capabilities": capabilities}


def _control_response(request_id: str, still_queued: list[str], subtype: str = "success") -> JSONObject:
    payload: JSONObject = {"still_queued": list(still_queued)}
    response: JSONObject = {"subtype": subtype, "request_id": request_id, "response": payload}
    return {"type": "control_response", "response": response}


def test_claude_dialogue_escalates_an_unread_injection_and_the_next_turn_carries_it() -> None:
    """段 10 lane 10n(実測 2026-09-14 場面 A): 道具の途中に注入した行は queued のまま → 停止の合図
    (control_request interrupt)→ 答えの still_queued に名指された注入は次の手番として走るので、止めた段の
    result(is_error)は手番の終わりとして報告しない(飲む)。started が読んだ印・次の result が手番の終わり。"""
    dialogue = ClaudeDialogue()
    assert dialogue.escalate().accepted is False  # 手番が走っていない
    dialogue.turn(_content("long tool"))
    dialogue.on_line(_init())
    assert dialogue.escalate().accepted is False  # queued の注入が無い
    injected = dialogue.inject(_content("stop now"), "msg-1")
    assert injected.ref == "msg-1"
    assert _record(injected.sends[0])["uuid"] == "msg-1"
    assert dialogue.on_line(_lifecycle("msg-1", "queued")).ended is None
    signal = dialogue.escalate()
    assert signal.accepted is True
    assert signal.sends == (claude_interrupt_request_line(signal.request_id),)
    assert _record(signal.sends[0]) == {
        "type": "control_request",
        "request_id": signal.request_id,
        "request": {"subtype": "interrupt"},
    }
    assert dialogue.escalate().accepted is False  # 出して答え待ち — 二度出さない
    assert dialogue.on_line(_control_response(signal.request_id, ["msg-1"])).ended is None
    assert dialogue.still_queued == ("msg-1",)
    swallowed = dialogue.on_line({"type": "result", "subtype": "error_during_execution", "is_error": True})
    assert swallowed.ended is None
    assert dialogue.in_flight is True
    assert dialogue.cli_turn_open is False
    assert dialogue.on_line(_lifecycle("msg-1", "started")).ended is None
    assert dialogue.injections == {"msg-1": "started"}
    assert dialogue.on_line(_init()).ended is None
    assert dialogue.cli_turn_open is True
    ended = dialogue.on_line({"type": "result", "subtype": "success", "is_error": False})
    assert ended.ended == TurnEnded(ok=True, detail="success")
    assert dialogue.in_flight is False
    # 手番の終わりの後の completed は知らない ref(空にした)— 何も起きない
    assert dialogue.on_line(_lifecycle("msg-1", "completed")).ended is None


def test_claude_dialogue_escalation_without_survivors_ends_the_turn_as_interrupted() -> None:
    """still_queued に注入が無い(abort の瞬間に畳みの途中だった)→ 次の手番は来ないので result で手番の終わり
    (interrupted)。答えが error なら合図は効かなかった — もう 1 度出せる。"""
    dialogue = ClaudeDialogue()
    dialogue.turn(_content("x"))
    dialogue.on_line(_init())
    dialogue.inject(_content("a"), "msg-1")
    first = dialogue.escalate()
    assert dialogue.on_line(_control_response(first.request_id, [], subtype="error")).ended is None
    assert dialogue.escalation is None
    second = dialogue.escalate()
    assert second.accepted is True
    assert second.request_id != first.request_id
    # 別の request_id の答えは無視する
    assert dialogue.on_line(_control_response("other", ["msg-1"])).ended is None
    assert dialogue.still_queued is None
    assert dialogue.on_line(_control_response(second.request_id, [])).ended is None
    ended = dialogue.on_line({"type": "result", "subtype": "error_during_execution", "is_error": True})
    assert ended.ended == TurnEnded(ok=False, detail="interrupted")
    assert dialogue.in_flight is False


def test_claude_dialogue_keeps_the_turn_while_an_injection_is_still_queued_at_the_result() -> None:
    """実測 2026-09-14 第 1 走: 道具の無い生成の途中に注入した行は畳まれず、result の後に次の手番として走る。
    result の時点で queued のままの注入が在れば手番は続く(誰の job でもない手番を作らない)。その注入が走らずに
    終わった(discarded / cancelled / refused)なら、そこで手番の終わり。"""
    dialogue = ClaudeDialogue()
    dialogue.turn(_content("x"))
    dialogue.on_line(_init())
    dialogue.inject(_content("late"), "msg-1")
    dialogue.on_line(_lifecycle("msg-1", "queued"))
    assert dialogue.on_line({"type": "result", "subtype": "success", "is_error": False}).ended is None
    assert dialogue.in_flight is True
    # 走らずに終わった → 手番の終わり(interrupt-discarded)
    ended = dialogue.on_line(_lifecycle("msg-1", "discarded"))
    assert ended.ended == TurnEnded(ok=False, detail="interrupt-discarded")
    assert dialogue.in_flight is False
    # 同じ形で started → init → result なら普通の終わり
    dialogue.turn(_content("y"))
    dialogue.inject(_content("late again"), "msg-2")
    dialogue.on_line(_lifecycle("msg-2", "queued"))
    assert dialogue.on_line({"type": "result", "subtype": "success", "is_error": False}).ended is None
    dialogue.on_line(_lifecycle("msg-2", "started"))
    dialogue.on_line(_init())
    # 手番が開いている間の cancelled(手番ごと abort された)は手番を閉じない — result が閉じる
    assert dialogue.on_line(_lifecycle("msg-2", "cancelled")).ended is None
    assert dialogue.on_line({"type": "result", "subtype": "success", "is_error": False}).ended == TurnEnded(
        ok=True, detail="success"
    )


def test_claude_dialogue_without_the_lifecycle_capability_injects_only_and_ends_on_result() -> None:
    """旧い CLI(init の capabilities に msg_lifecycle_v1 が無い)は注入の行の運命を名乗らない — 注入は受けるが追わず
    (result で手番が終わる・queued のままの注入を待って result を飲まない・停止の合図は出さない = 今日どおりの注入だけ)。
    capabilities の無い init も同じ(名乗らない = 無い)。"""
    bare: JSONObject = {"type": "system", "subtype": "init", "session_id": "sid-1"}
    for init in (_init(lifecycle=False), bare):
        dialogue = ClaudeDialogue()
        dialogue.turn(_content("x"))
        dialogue.on_line(init)
        assert dialogue.lifecycle is False
        injected = dialogue.inject(_content("stop"), "msg-1")
        assert injected.accepted is True
        assert _record(injected.sends[0])["uuid"] == "msg-1"
        assert dialogue.injections == {}
        assert dialogue.escalate().accepted is False
        assert dialogue.on_line({"type": "result", "subtype": "success", "is_error": False}).ended == TurnEnded(ok=True, detail="success")
        assert dialogue.in_flight is False


def test_codex_dialogue_does_not_escalate() -> None:
    """確定 4: codex に注入の段は無い(inject が turn/interrupt で止めて渡す・能力 stop)— 停止の合図は出す物が無い。"""
    dialogue = CodexDialogue(CodexPlan(cwd="/w"))
    dialogue.opening()
    dialogue.on_line({"id": "initialize#1", "result": {}})
    dialogue.on_line({"id": "thread/start#2", "result": {"thread": {"id": "thr"}}})
    dialogue.turn(_content("go"))
    dialogue.on_line({"method": "turn/started", "params": {"threadId": "thr", "turn": {"id": "t1"}}})
    injected = dialogue.inject(_content("stop"), "msg-1")
    assert injected.accepted is True
    assert injected.ref == "msg-1"
    assert dialogue.escalate().accepted is False


def _rpc(line: str) -> JSONObject:
    return _record(line)


def test_codex_dialogue_handshake_turn_and_interrupt() -> None:
    dialogue = CodexDialogue(CodexPlan(cwd="/w", model="gpt-5", effort="high"))
    opening = dialogue.opening()
    assert [_rpc(line)["method"] for line in opening] == ["initialize"]
    # prompt は thread が開くまで積む
    assert dialogue.turn(_content("hi")).sends == ()
    init_reply = dialogue.on_line({"id": _rpc(opening[0])["id"], "result": {}})
    assert [_rpc(line)["method"] for line in init_reply.sends] == ["initialized", "thread/start"]
    thread_start = _rpc(init_reply.sends[1])
    assert thread_start["params"] == {
        "approvalPolicy": "never",
        "sandbox": "danger-full-access",
        "cwd": "/w",
        "model": "gpt-5",
    }
    opened = dialogue.on_line({"id": thread_start["id"], "result": {"thread": {"id": "thr-1"}}})
    assert opened.conversation == {"session_id": "thr-1"}
    assert len(opened.sends) == 1
    turn_start = _rpc(opened.sends[0])
    assert turn_start["method"] == "turn/start"
    turn_params = _obj(turn_start, "params")
    assert turn_params["threadId"] == "thr-1"
    assert turn_params["input"] == [{"type": "text", "text": "hi"}]
    assert turn_params["effort"] == "high"
    assert turn_params["sandboxPolicy"] == {"type": "dangerFullAccess"}
    # 手番が始まる前は割り込む turn が無い
    assert dialogue.interrupt().sends == ()
    dialogue.on_line({"id": turn_start["id"], "result": {"turn": {"id": "turn-1"}}})
    interrupt = dialogue.interrupt()
    assert _rpc(interrupt.sends[0])["params"] == {"threadId": "thr-1", "turnId": "turn-1"}
    # 別の turn の完了は採らない
    other = dialogue.on_line(
        {
            "method": "turn/completed",
            "params": {"threadId": "thr-1", "turn": {"id": "turn-9", "status": "completed"}},
        }
    )
    assert other.ended is None
    mine = dialogue.on_line(
        {
            "method": "turn/completed",
            "params": {"threadId": "thr-1", "turn": {"id": "turn-1", "status": "interrupted"}},
        }
    )
    assert mine.ended == TurnEnded(ok=False, detail="turn-interrupted")
    # 温かい process: 次の手番は直ちに turn/start
    second = dialogue.turn(_content("again"))
    assert _rpc(second.sends[0])["method"] == "turn/start"
    assert second.close_stdin is False


def test_codex_dialogue_inject_interrupts_then_starts_the_next_turn_as_one_turn() -> None:
    """段 8 lane 4x: 割り込みの本文 = turn/interrupt → interrupted の turn/completed を手番の終わりと
    報告せず、同じ thread へ本文の turn/start(host から見て手番は 1 つのまま)。"""
    dialogue = CodexDialogue(CodexPlan(cwd="/w"))
    opening = dialogue.opening()
    assert dialogue.inject(_content("early")).accepted is False  # thread も turn も無い
    init_reply = dialogue.on_line({"id": _rpc(opening[0])["id"], "result": {}})
    thread_start = _rpc(init_reply.sends[1])
    opened = dialogue.on_line({"id": thread_start["id"], "result": {"thread": {"id": "thr-1"}}})
    assert opened.sends == ()
    assert dialogue.inject(_content("still early")).accepted is False  # turn が走っていない
    turn_start = _rpc(dialogue.turn(_content("work")).sends[0])
    dialogue.on_line({"id": turn_start["id"], "result": {"turn": {"id": "turn-1"}}})
    injected = dialogue.inject(_content("change course"))
    assert injected.accepted is True
    assert [_rpc(line)["method"] for line in injected.sends] == ["turn/interrupt"]
    assert _obj(_rpc(injected.sends[0]), "params") == {"threadId": "thr-1", "turnId": "turn-1"}
    # 2 通目の割り込みは止める合図を重ねず本文を継ぎ足す
    second = dialogue.inject(_content("and this"))
    assert second.accepted is True
    assert second.sends == ()
    completed = dialogue.on_line(
        {
            "method": "turn/completed",
            "params": {"threadId": "thr-1", "turn": {"id": "turn-1", "status": "interrupted"}},
        }
    )
    assert completed.ended is None  # 手番の終わりではない
    assert [_rpc(line)["method"] for line in completed.sends] == ["turn/start"]
    restarted = _obj(_rpc(completed.sends[0]), "params")
    assert restarted["threadId"] == "thr-1"
    assert restarted["input"] == [{"type": "text", "text": "change course\n\nand this"}]
    dialogue.on_line({"id": _rpc(completed.sends[0])["id"], "result": {"turn": {"id": "turn-2"}}})
    mine = dialogue.on_line(
        {
            "method": "turn/completed",
            "params": {"threadId": "thr-1", "turn": {"id": "turn-2", "status": "completed"}},
        }
    )
    assert mine.ended == TurnEnded(ok=True, detail="completed")
    assert dialogue.inject(_content("late")).accepted is False


def test_codex_dialogue_refuses_unsupported_server_request_and_interrupts() -> None:
    dialogue = CodexDialogue(CodexPlan(cwd="/w", resume_thread_id="thr-old"))
    opening = dialogue.opening()
    init_reply = dialogue.on_line({"id": _rpc(opening[0])["id"], "result": {}})
    assert _rpc(init_reply.sends[1])["method"] == "thread/resume"
    assert _obj(_rpc(init_reply.sends[1]), "params")["threadId"] == "thr-old"
    dialogue.on_line(
        {"id": _rpc(init_reply.sends[1])["id"], "result": {"thread": {"id": "thr-old"}}}
    )
    started = dialogue.turn(_content("go"))
    dialogue.on_line({"id": _rpc(started.sends[0])["id"], "result": {"turn": {"id": "t1"}}})
    accepted = dialogue.on_line(
        {
            "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thr-old", "turnId": "t1"},
        }
    )
    assert _rpc(accepted.sends[0]) == {"jsonrpc": "2.0", "id": 7, "result": {"decision": "accept"}}
    refused = dialogue.on_line(
        {
            "id": 8,
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "thr-old", "turnId": "t1"},
        }
    )
    assert refused.failure == "unsupported-server-request:item/tool/requestUserInput"
    assert _obj(_rpc(refused.sends[0]), "error")["code"] == -32001
    assert _rpc(refused.sends[1])["method"] == "turn/interrupt"
    completed = dialogue.on_line(
        {
            "method": "turn/completed",
            "params": {"threadId": "thr-old", "turn": {"id": "t1", "status": "completed"}},
        }
    )
    # 断った手番は後の completed でも成功にならない
    assert completed.ended == TurnEnded(
        ok=False, detail="unsupported-server-request:item/tool/requestUserInput"
    )


@pytest.mark.parametrize(
    ("observation", "in_flight", "kind", "ok"),
    [
        (None, True, "gone", False),
        (None, False, "idle", True),
        (HeadlessObservation(alive=True, exit_code=None), True, "running", True),
        (HeadlessObservation(alive=False, exit_code=0), False, "idle", True),
        (
            HeadlessObservation(alive=False, exit_code=0, ended=(TurnEnded(True, "success"),)),
            True,
            "turn-ended",
            True,
        ),
        (
            HeadlessObservation(alive=True, exit_code=None, failure="thread-start-failed: x"),
            True,
            "failed",
            False,
        ),
        (HeadlessObservation(alive=False, exit_code=1), True, "failed", False),
        (
            HeadlessObservation(alive=False, exit_code=-2, interrupted=True),
            True,
            "turn-ended",
            False,
        ),
    ],
)
def test_turn_verdict_closed_vocabulary(
    observation: HeadlessObservation | None, in_flight: bool, kind: str, ok: bool
) -> None:
    verdict = turn_verdict(observation, in_flight)
    assert verdict.kind == kind
    assert verdict.ok is ok


@pytest.mark.parametrize(
    ("terminal", "in_flight", "liveness", "kind"),
    [
        # 終端の行は観測に依らず keep
        (True, True, BackendLiveness(pid=1, exists=False, owned=False), "keep"),
        (True, False, BackendLiveness(pid=1, exists=False, owned=False), "keep"),
        # idle の温かい行 ∧ backend が生きて所有 → keep(器が在る)
        (False, False, BackendLiveness(pid=1, exists=True, owned=True), "keep"),
        # idle の温かい行 ∧ backend が死んでいる → backend-dead(2026-09-22 の改訂・R25 の追補:
        # 旧形はここを keep にしていたので、器を 1 つも持たない機体が observations.sessions に
        # 「走っている session」として名乗った)
        (False, False, BackendLiveness(pid=1, exists=False, owned=False), "backend-dead"),
        (False, False, BackendLiveness(pid=7, exists=True, owned=False), "backend-dead"),
        # 手番の途中 ∧ backend が生きて所有 → keep
        (False, True, BackendLiveness(pid=1, exists=True, owned=True), "keep"),
        # 手番の途中 ∧ pid が無い → backend-dead
        (False, True, BackendLiveness(pid=22663, exists=False, owned=False), "backend-dead"),
        # 手番の途中 ∧ pid は在るが所有でない(親を失った孤児)→ backend-dead
        (False, True, BackendLiveness(pid=22663, exists=True, owned=False), "backend-dead"),
        # 手番の途中 ∧ backend_ref に pid が無い → backend-dead
        (False, True, BackendLiveness(pid=None, exists=False, owned=False), "backend-dead"),
    ],
)
def test_recovery_verdict_is_the_one_decision(
    terminal: bool, in_flight: bool, liveness: BackendLiveness, kind: str
) -> None:
    """段 10 lane 10h(agora-redesign #84)+ 2026-09-22 の改訂(card acp:kanban-issue:ki-95169e9e265d 便 1):
    起動時の復帰の判断は recovery_verdict の 1 点 — 非終端 ∧ backend が死んでいる(pid が無い / この host の
    所有でない)行を終端に倒す。手番の途中かどうかは倒すかどうかを決めず、detail の文だけを分ける。"""
    verdict = recovery_verdict(terminal, in_flight, liveness)
    assert verdict.kind == kind
    if kind == "backend-dead":
        assert "backend process dead" in verdict.detail
        assert f"pid {liveness.pid if liveness.pid is not None else 'none'}" in verdict.detail
        assert ("not owned" in verdict.detail) is liveness.exists
        # 手番の途中と idle は同じ判断・違う文(cause の reason が何を失ったかを名乗る)。
        assert ("while the turn was in flight" in verdict.detail) is in_flight
        assert ("the row was idle between turns" in verdict.detail) is (not in_flight)
    assert backend_alive(liveness) is (liveness.exists and liveness.owned)


def test_recovery_verdict_folds_the_idle_warm_row_whose_backend_is_gone() -> None:
    """2026-09-22(R25 の改訂): 器の死んだ idle の温かい行は「走っている session」ではない — 倒して
    transcripts の半分へ移す(次の手番は next-arm-for-job の terminal ∧ same-home の腕で --resume)。
    倒さないと、pod の家が永続した機体が死んだ器を observations.sessions に名乗り、その欄だけを読む
    keepalive が死んだ器へ ping を送る。"""
    dead = BackendLiveness(pid=22663, exists=False, owned=False)
    assert recovery_verdict(False, False, dead).kind == "backend-dead"
    # 生きている器は idle でも触らない(次の send がそのまま届く)。
    assert recovery_verdict(False, False, BackendLiveness(pid=1, exists=True, owned=True)).kind == "keep"
    # 終端の行は観測に依らず触らない。
    assert recovery_verdict(True, False, dead).kind == "keep"


def test_stop_verdict_cuts_only_the_mid_turn_rows() -> None:
    """段 10 lane 10h 便 2: host の停止の前の判断は stop_verdict の 1 点 — 手番の途中の非終端の行だけ turn-cut。"""
    assert stop_verdict(False, True) == "turn-cut"
    assert stop_verdict(False, False) == "keep"
    assert stop_verdict(True, True) == "keep"
    assert stop_verdict(True, False) == "keep"


def test_stop_cause_category_names_the_planned_stop_only_under_the_drain_marker() -> None:
    """card acp:kanban-issue:ki-b5e0d04de958 D1(受入 1): 停止で切った行の語は stop_cause_category の 1 点 —
    停止の拍に排水の印が在った = host_drained / 無い = cancelled(今日の語のまま)。2 語とも policy の閉語彙に在り、
    host_drained は走らせ直せる(retryable)。"""
    from doeff_agents.sessionhost.policy import TERMINAL_CAUSE_RETRYABLE

    assert stop_cause_category(True) == "host_drained"
    assert stop_cause_category(False) == "cancelled"
    assert TERMINAL_CAUSE_RETRYABLE["host_drained"] is True
    assert TERMINAL_CAUSE_RETRYABLE["cancelled"] is False


def test_the_drain_marker_is_read_by_presence_and_its_line_is_only_a_reason(tmp_path: Path) -> None:
    """drain_marker(設計の改訂 1a): 印ありの答えは在否だけ(中身が false でも在れば印あり)・path なし = 印なし・
    中身は 1 行目を理由として返すだけ(読めない・空 = "")。"""
    from doeff_agents.sessionhost import drain_marker

    marker = tmp_path / "drain"
    assert drain_marker.declared(None) is False
    assert drain_marker.declared(str(marker)) is False
    assert drain_marker.reason_line(str(marker)) == ""
    assert drain_marker.reason_line(None) == ""
    marker.write_text("false\n", encoding="utf-8")
    assert drain_marker.declared(str(marker)) is True
    marker.write_text("pool-prestop agentd-pool-1 u-1 2026-09-24T00:00:00Z pod termination\nsecond\n", encoding="utf-8")
    assert drain_marker.reason_line(str(marker)) == "pool-prestop agentd-pool-1 u-1 2026-09-24T00:00:00Z pod termination"
    marker.write_text("", encoding="utf-8")
    assert drain_marker.declared(str(marker)) is True
    assert drain_marker.reason_line(str(marker)) == ""


def test_the_host_reads_the_drain_marker_path_only_from_its_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """設計の改訂 1c: host は印の path を env DOEFF_SESSIONHOST_DRAIN_FILE からだけ読む(CLI の語彙は凍結 —
    argv に flag を足さない)。空・無し = None(印を読まない起動)。"""
    argv = ["--db", "/tmp/x.sqlite", "--socket", "/tmp/x.sock", "--backend", "headless", "serve"]
    monkeypatch.setenv(host.ENV_DRAIN_FILE, "/var/lib/agentd/drain")
    assert host.parse_args(argv).drain_file == "/var/lib/agentd/drain"
    monkeypatch.setenv(host.ENV_DRAIN_FILE, "")
    assert host.parse_args(argv).drain_file is None
    monkeypatch.delenv(host.ENV_DRAIN_FILE)
    assert host.parse_args(argv).drain_file is None
    with pytest.raises(ValueError, match="unknown argument: --drain-file"):
        host.parse_args(["--drain-file", "/var/lib/agentd/drain", *argv])


@pytest.mark.parametrize("first_declared", [False, True])
def test_the_term_handler_reads_the_marker_once_at_the_first_term_and_never_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_declared: bool
) -> None:
    """card acp:kanban-issue:ki-b5e0d04de958(受入 2 の (d)・設計の改訂の残り): 印を読むのは 1 度目の TERM の拍に 1 回 —
    答えは値のまま停止の腕へ渡り、1 度目の後に印が立っても・消えても、2 度目の TERM(撃ち直し・停止中の再送)は
    SystemExit(0) だけで読み直さない。実 binary の TERM は handler と停止の thread の間の順序を外から挟めないので、
    ここは handler そのものを直に撃つ(読む点が handler の 1 か所だけであることは ADR-DOE-AGENTS-012 の針が撃つ)。"""
    marker = tmp_path / "drain"
    if first_declared:
        marker.write_text("pool-prestop agentd-pool-1 u-1 2026-09-24T00:00:00Z pod termination\n", encoding="utf-8")
    monkeypatch.setenv(host.ENV_DRAIN_FILE, str(marker))
    config = host.parse_args(["--db", str(tmp_path / "x.sqlite"), "--socket", str(tmp_path / "x.sock"), "serve"])
    started: list[tuple[int, bool, str]] = []
    handler = host.term_handler(config, lambda signum, declared, line: started.append((signum, declared, line)))
    handler(signal.SIGTERM, None)
    first = started[0]
    assert first[1] is first_declared
    assert (first[2] != "") is first_declared
    if first_declared:
        marker.unlink()
    else:
        marker.write_text("operator hold\n", encoding="utf-8")
    with pytest.raises(SystemExit) as again:
        handler(signal.SIGTERM, None)
    assert again.value.code == 0
    assert started == [first]


def test_terminal_cause_from_dict_is_total_over_the_store() -> None:
    """段 10 lane 10h(agora-redesign #84 副次): 契約の欄を持たない persisted cause(手で書かれた
    {"cause": …})は typed には None — 行ごと読めなくなる KeyError 'category'(store.hy の decode が根)
    を出さない。契約どおりの payload はそのまま TerminalCause。"""
    assert terminal_cause_from_dict({"cause": "backend-process-dead", "pid": 22663}) is None
    assert terminal_cause_from_dict({"category": "vanished"}) is None  # observed_at 無し
    cause = terminal_cause_from_dict(
        {"category": "vanished", "reason": "r", "retryable": True, "observed_at": "2026-09-14T00:00:00+00:00"}
    )
    assert cause is not None
    assert cause.category == "vanished"
    assert cause.retryable is True


def test_headless_argv_is_print_mode_with_partial_messages() -> None:
    fresh = _build_claude_headless(
        {"work_dir": "/w", "session_hooks": "disabled", "conversation": {"session_id": "sid-1"}}
    )
    argv = fresh["argv"]
    assert isinstance(argv, list)
    assert argv[:8] == [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    assert argv[-2:] == ["--session-id", "sid-1"]
    assert isinstance(fresh["dialogue"], ClaudeDialogue)
    resumed = _build_claude_headless(
        {"work_dir": "/w", "resume_mode": "resume", "conversation": {"session_id": "sid-1"}}
    )
    resumed_argv = resumed["argv"]
    assert isinstance(resumed_argv, list)
    assert resumed_argv[-2:] == ["--resume", "sid-1"]
    assert "--session-id" not in resumed_argv
    # 冷えた再開の前の圧縮の argv: 同じ基礎の旗 + print mode の prompt 1 つ + 同じ --resume・
    # stream-json の旗は無い。fresh(初手番)には無い(圧縮する歴史が無い)。
    cold = resumed["cold_compaction_argv"]
    assert isinstance(cold, list)
    assert cold[0] == "claude"
    assert cold[-4:] == ["-p", "/compact fast-jev-if-cold", "--resume", "sid-1"]
    assert "stream-json" not in cold and "--session-id" not in cold
    assert "cold_compaction_argv" not in fresh
    # disableAllHooks は載せない(plugin の hook を殺すと /compact が組込みの要約に落ちる)。他に欄が無ければ
    # --settings の旗ごと消える。手番の argv には今日どおり disableAllHooks が在る。
    assert "--settings" not in cold, cold
    assert "disableAllHooks" in json.loads(resumed_argv[resumed_argv.index("--settings") + 1])
    # 自動記憶の置き場など他の欄は保つ
    with_memory = _build_claude_headless(
        {"work_dir": "/w", "resume_mode": "resume", "conversation": {"session_id": "sid-1"},
         "memory_dir": "/m"}
    )
    cold_memory = with_memory["cold_compaction_argv"]
    assert cold_memory.count("--settings") == 1
    assert json.loads(cold_memory[cold_memory.index("--settings") + 1]) == {
        claude_code.CLAUDE_AUTO_MEMORY_DIR_SETTING: "/m"  # 綴りの家は impls/claude_code.hy の 1 点
    }
    codex = _build_codex_headless(
        {"work_dir": "/w", "model": "gpt-5", "effort": "high"}
    )
    assert codex["argv"] == [
        "codex",
        "-c",
        'model_reasoning_effort="high"',
        "app-server",
        "--listen",
        "stdio://",
    ]
    codex_dialogue = codex["dialogue"]
    assert isinstance(codex_dialogue, CodexDialogue)
    assert codex_dialogue.plan.model == "gpt-5"


def test_headless_claude_cannot_start_background_tasks_that_outlive_the_turn() -> None:
    """1 手番 1 process(R48)の器は result の行で process を降ろすので、CLI が手番の外へ持ち越す background の仕事
    (Agent の run_in_background・Bash の background・Monitor)は降ろした時に黙って死ぬ。実弾 2026-09-23: 計画の会話
    (c-SKD631B1SP… / c-3MZFCNDVHE…)が盲検 A・B の subagent を background に回し「返答待ち」で手番を終え、依頼が開いた
    まま担い手が静止した。⇒ headless の claude の argv は全部の腕(初手番・続き・cache の ping・席の settings の合流)で
    CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 を 1 つの --settings の env に持つ。反例 = どれかの腕で env が無ければ赤。"""
    key = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"
    arms = {
        "fresh": {"work_dir": "/w", "conversation": {"session_id": "sid-1"}},
        "fresh-hooks-disabled": {"work_dir": "/w", "session_hooks": "disabled", "conversation": {"session_id": "sid-1"}},
        "resume": {"work_dir": "/w", "resume_mode": "resume", "conversation": {"session_id": "sid-1"}, "memory_dir": "/m"},
        "cache-ping": {"work_dir": "/w", "resume_mode": "resume", "conversation": {"session_id": "sid-1"},
                       "cache_maintenance": True},
    }
    for name, params in arms.items():
        argv = _build_claude_headless(params)["argv"]
        assert argv.count("--settings") == 1, (name, argv)
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert settings.get("env", {}).get(key) == "1", (name, settings)
    # 既存の欄は保つ(自動記憶の置き場・cache の ping の disableAllHooks)
    resumed = _build_claude_headless(arms["resume"])["argv"]
    assert json.loads(resumed[resumed.index("--settings") + 1])[claude_code.CLAUDE_AUTO_MEMORY_DIR_SETTING] == "/m"
    ping = _build_claude_headless(arms["cache-ping"])["argv"]
    assert json.loads(ping[ping.index("--settings") + 1])["disableAllHooks"] is True
    # 純関数の合流: 既に在る env の他の鍵は残り、同じ鍵は headless の値で上書き
    merged = run(headless_argv.argv_with_settings_env(
        ["claude", "--settings", json.dumps({"env": {"A": "x", key: "0"}})], {key: "1"}))
    assert json.loads(merged[2]) == {"env": {"A": "x", key: "1"}}


def test_headless_claude_declares_the_compaction_threshold_on_both_arms() -> None:
    """会話の圧縮の閾値(設計記録 docs/design/auto-compact-window): ACP の本番の腕は headless backend なので、閾値が
    **この経路で**載ることを固定する。実弾 2026-09-18: 稼働中の claude 席 43 本のうち
    --autocompact を持つものが 0 本で、CLI が自分の窓(1M)いっぱいまで畳まずに伸びていた
    (1 手番の平均の文脈 550k・最大 967k)。値は charter.auto_compact_window、
    無ければ走行係の床。"""
    fresh = _build_claude_headless(
        {"work_dir": "/w", "conversation": {"session_id": "sid-1"}}
    )["argv"]
    assert "--autocompact" in fresh, fresh
    assert fresh[fresh.index("--autocompact") + 1] == "400000", fresh

    declared = _build_claude_headless(
        {"work_dir": "/w", "auto_compact_window": 200000, "conversation": {"session_id": "sid-1"}}
    )["argv"]
    assert declared[declared.index("--autocompact") + 1] == "200000", declared

    resumed = _build_claude_headless(
        {
            "work_dir": "/w",
            "auto_compact_window": 200000,
            "resume_mode": "resume",
            "conversation": {"session_id": "sid-1"},
        }
    )["argv"]
    assert resumed[resumed.index("--autocompact") + 1] == "200000", resumed
    assert resumed[-2:] == ["--resume", "sid-1"], resumed

    # 幅の外の値は argv に出さない(出すと CLI が argv 解釈の段で死に、手番が 1 行も吐かない)
    degraded = _build_claude_headless(
        {"work_dir": "/w", "auto_compact_window": 2_000_000, "conversation": {"session_id": "sid-1"}}
    )["argv"]
    assert degraded[degraded.index("--autocompact") + 1] == "auto", degraded


#: dotfiles の router shim(agentcli/codex_shim.py _is_forbidden_override)が政策違反として
#: 拒む綴り(2026-09-12 の実弾: PATH の codex が shim で `--yolo` が exit 2)。shim の module は
#: import しない — 検は綴りの写しを持ち、shim と同じ語彙であることを註で結ぶ。
SHIM_FORBIDDEN_FLAGS = ("-s", "--sandbox", "-a", "--full-auto", "--yolo")
SHIM_FORBIDDEN_PREFIXES = ("--sandbox=", "-s=", "--ask-for-approval=", "-a=")
SHIM_FORBIDDEN_CONFIG_KEYS = (
    "sandbox_mode=",
    "sandbox_permissions=",
    "sandbox_workspace_write.",
    "approval_policy=",
)


def _shim_would_refuse(argv: list[str]) -> list[str]:
    """router shim が拒む要素(1 要素の述語 + `-c` の直後の本文)。"""
    refused: list[str] = []
    after_config = False
    for arg in argv:
        if (
            arg in SHIM_FORBIDDEN_FLAGS
            or arg.startswith(SHIM_FORBIDDEN_PREFIXES)
            or (after_config and arg.startswith(SHIM_FORBIDDEN_CONFIG_KEYS))
        ):
            refused.append(arg)
        after_config = arg in ("-c", "--config")
    return refused


def test_codex_headless_argv_carries_no_override_the_router_shim_refuses() -> None:
    """agora-redesign #37 lane 2d-2: 全面許可の旗は argv に載せない(shim が exit 2 で拒む)。
    方策は app-server の thread / turn の params が正本 — 綴りは shim の
    full_access_app_server と同値(headless_protocol.THREAD_FULL_ACCESS / TURN_FULL_ACCESS)。"""
    built = _build_codex_headless(
        {
            "work_dir": "/w",
            "model": "gpt-5",
            "effort": "high",
            "mcp_servers": {"caller": "http://127.0.0.1:1/mcp"},
            "result_channel": {"command": "doeff-report", "args": ["--sock", "/s"]},
        }
    )
    argv = built["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "codex"
    assert argv[-3:] == ["app-server", "--listen", "stdio://"]
    assert _shim_would_refuse(argv) == []
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv  # shim 自身が足す正規形
    # `-c` の対(effort・caller mcp・result channel)はそのまま — shim は sandbox / approval だけ拒む
    configs = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
    assert configs[0] == 'model_reasoning_effort="high"'
    assert any(body.startswith('mcp_servers."caller".url=') for body in configs)
    assert any(body.startswith('mcp_servers."doeff_result".command=') for body in configs)
    assert "--model" not in argv  # model は thread の params
    # 方策は params で運ぶ: thread/start
    dialogue = built["dialogue"]
    assert isinstance(dialogue, CodexDialogue)
    opening = dialogue.opening()
    init_reply = dialogue.on_line({"id": _rpc(opening[0])["id"], "result": {}})
    thread_start = _rpc(init_reply.sends[1])
    assert thread_start["method"] == "thread/start"
    thread_params = _obj(thread_start, "params")
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["sandbox"] == "danger-full-access"
    assert thread_params["model"] == "gpt-5"
    # turn/start
    dialogue.on_line({"id": thread_start["id"], "result": {"thread": {"id": "thr-1"}}})
    turn_start = _rpc(dialogue.turn(_content("hi")).sends[0])
    assert turn_start["method"] == "turn/start"
    assert _obj(turn_start, "params")["sandboxPolicy"] == {"type": "dangerFullAccess"}
    # 続きの手番(resume)の thread/resume も同じ方策
    resumed = _build_codex_headless(
        {"work_dir": "/w", "resume_mode": "resume", "conversation": {"session_id": "thr-old"}}
    )
    resumed_argv = resumed["argv"]
    assert isinstance(resumed_argv, list)
    assert _shim_would_refuse(resumed_argv) == []
    resumed_dialogue = resumed["dialogue"]
    assert isinstance(resumed_dialogue, CodexDialogue)
    resume_opening = resumed_dialogue.opening()
    resume_reply = resumed_dialogue.on_line({"id": _rpc(resume_opening[0])["id"], "result": {}})
    thread_resume = _rpc(resume_reply.sends[1])
    assert thread_resume["method"] == "thread/resume"
    resume_params = _obj(thread_resume, "params")
    assert resume_params["threadId"] == "thr-old"
    assert resume_params["approvalPolicy"] == "never"
    assert resume_params["sandbox"] == "danger-full-access"


# ---------------------------------------------------------------- 2. 器(実 process の替え玉)


def _stub_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{STUBS}{os.pathsep}{env.get('PATH', '')}"
    env.update(extra or {})
    return env


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        _pause(0.02)
    raise AssertionError("condition did not hold in time")


def test_headless_process_claude_carries_the_image_to_the_stub_cli(tmp_path: Path) -> None:
    """段 10 lane 10o(agora-redesign #96): 型つきの添付を deliver に渡すと、Dialogue が組んだ image の
    block が実 process(替え玉 CLI)に届く。替え玉は綴りが実測どおりの時だけ読み、答えに何を見たかを言う
    (綴りが違えば替え玉が落ちる = 検が赤)。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s-img.events.jsonl")
    process = registry.spawn(
        "s-img",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-img"],
        str(tmp_path),
        _stub_env(),
        events,
        ClaudeDialogue(),
    )
    assert process.deliver("この色は", (_png(),)) is True
    _wait_until(lambda: len(process.peek_records()) >= 5)
    observed = process.observe()
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    said = [
        str(_obj(record, "message").get("content"))
        for record in observed.records
        if record.get("type") == "assistant"
    ]
    assert any(f"[saw image/png {len(PNG_B64)}b]" in text for text in said), said
    registry.kill("s-img")


def test_headless_process_codex_carries_the_image_to_the_stub_app_server(tmp_path: Path) -> None:
    """段 10 lane 10o: codex の替え玉(app-server)も data URL の image の項だけを読む。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s-cimg.events.jsonl")
    process = registry.spawn(
        "s-cimg",
        ["codex", "app-server", "--listen", "stdio://"],
        str(tmp_path),
        _stub_env(),
        events,
        CodexDialogue(CodexPlan(cwd=str(tmp_path))),
    )
    assert process.deliver("この色は", (_png(),)) is True
    _wait_until(lambda: any(
        "agentMessage" in json.dumps(record) for record in process.peek_records()
    ))
    said = json.dumps(process.peek_records(), ensure_ascii=False)
    assert f"[saw image/png {len(PNG_B64)}b]" in said, said
    registry.kill("s-cimg")


def test_headless_process_claude_turn_writes_events_and_retires_at_the_result(tmp_path: Path) -> None:
    """段 12 lane 12e(agora-redesign #517): 手番の終わり = process の終わり。器は result の行で stdin に EOF を
    出し(retire)、process は降りる — 次の手番は同じ process へは書けない(--resume の新しい process)。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s1.events.jsonl")
    process = registry.spawn(
        "s1",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-1"],
        str(tmp_path),
        _stub_env(),
        events,
        ClaudeDialogue(),
    )
    assert process.retired is False
    assert process.deliver("hello there") is True
    _wait_until(lambda: len(process.peek_records()) >= 5)
    assert process.retired is True  # result の行で降ろし始めた(拍を待たない)
    _wait_until(lambda: not process.alive())
    observed = process.observe()
    assert observed.alive is False
    assert observed.exit_code == 0  # EOF で自分で降りた(梯子の SIGTERM は要らなかった)
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    assert observed.conversation == {"session_id": "sid-1"}
    assert observed.accepts_turn is False
    kinds = [str(record.get("type")) for record in observed.records]
    assert kinds == ["system", "stream_event", "stream_event", "assistant", "result"]
    lines = Path(events).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert json.loads(lines[-1])["type"] == "result"
    assert turn_verdict(observed, True).kind == "turn-ended"
    # 降りた process は手番の外の割り込みも次の手番も引き受けない(次は --resume の起こし直し)
    assert process.inject("nothing runs") is False
    assert process.deliver("again") is False
    assert turn_verdict(process.observe(), False).kind == "idle"
    # 降りた process の登記は同じ名で置き換えられる(--resume の起こし直しの器の物理)
    resumed = registry.spawn(
        "s1",
        ["claude", "-p", "--input-format", "stream-json", "--resume", "sid-1"],
        str(tmp_path),
        _stub_env(),
        events,
        ClaudeDialogue(),
    )
    assert resumed.pid != process.pid
    assert resumed.deliver("again") is True
    _wait_until(lambda: len(Path(events).read_text(encoding="utf-8").splitlines()) >= 10)
    _wait_until(lambda: not resumed.alive())
    assert len(Path(events).read_text(encoding="utf-8").splitlines()) == 10
    assert json.loads(Path(events).read_text(encoding="utf-8").splitlines()[5])["resumed"] is True
    assert registry.kill("s1") is True


def test_headless_process_claude_result_closes_the_dialogue_before_the_cli_can_reenter(tmp_path: Path) -> None:
    """反例(agora-redesign #517・実弾 2026-09-17 19:4x): 実物の CLI は result の後も stdin が開いている限り
    生きて、background task / Monitor の完了で model を手番の外で起こし直し tool を撃つ(同じ会話の 2 つの
    process が本番に作用・記録に載らない行動)。替え玉は DOEFF_HEADLESS_STUB_REENTER_AFTER 秒の内に EOF が
    来なければその再入(assistant の tool_use + 2 つ目の result)を出す。直し = 器が result の行で EOF を出す
    ので、再入は 1 行も出ず process は降りる。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s-re.events.jsonl")
    process = registry.spawn(
        "s-re",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-re"],
        str(tmp_path),
        _stub_env({"DOEFF_HEADLESS_STUB_REENTER_AFTER": "0.5"}),
        events,
        ClaudeDialogue(),
    )
    assert process.deliver("run a build in the background and end the turn") is True
    _wait_until(lambda: not process.alive(), timeout=5.0)
    _pause(0.8)  # 再入の期限(0.5 秒)を過ぎても、降りた process は何も出せない
    records = [json.loads(line) for line in Path(events).read_text(encoding="utf-8").splitlines()]
    assert [str(r.get("type")) for r in records] == ["system", "stream_event", "stream_event", "assistant", "result"]
    assert not any(r.get("reentered") for r in records)
    assert not any(
        block.get("type") == "tool_use"
        for r in records if r.get("type") == "assistant"
        for block in (r.get("message") or {}).get("content", []) if isinstance(block, dict)
    )
    observed = process.observe()
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    assert observed.exit_code == 0


def test_headless_process_claude_resume_runs_the_prompt_past_the_cli_own_turn(
    tmp_path: Path,
) -> None:
    """依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(実弾 2026-09-19 07:28 JST・pod j4bz4): ``--resume`` で起きた CLI は本文より
    先に孤児の task の報せを自分の手番として走らせる(替え玉 DOEFF_HEADLESS_STUB_ORPHAN_NOTICES)。器はその result で
    降ろさず、本文の手番を最後まで走らせる — 手番の終わりは 1 つだけで、本文への応答(assistant)が記録に在る。
    旧形は CLI 自身の result で stdin を閉じ、本文の手番(2 つ目の init)が切られて出力 0 件の手番になった。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s-orphan.events.jsonl")
    process = registry.spawn(
        "s-orphan",
        ["claude", "-p", "--input-format", "stream-json", "--resume", "sid-orphan"],
        str(tmp_path),
        _stub_env({"DOEFF_HEADLESS_STUB_ORPHAN_NOTICES": "3"}),
        events,
        ClaudeDialogue(),
    )
    assert process.deliver("the mail the turn carries") is True
    _wait_until(lambda: not process.alive(), timeout=5.0)
    observed = process.observe()
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    assert observed.exit_code == 0
    records = [json.loads(line) for line in Path(events).read_text(encoding="utf-8").splitlines()]
    shape = [
        f"{r.get('type')}/{r.get('subtype')}" if r.get("type") == "system" else str(r.get("type"))
        for r in records
    ]
    assert shape == [
        "system/task_notification",
        "system/task_notification",
        "system/task_notification",
        "system/init",
        "result",
        "system/init",
        "stream_event",
        "stream_event",
        "assistant",
        "result",
    ]
    assert records[4]["origin"] == {"kind": "task-notification"}
    said = [
        str(_obj(record, "message").get("content"))
        for record in records
        if record.get("type") == "assistant"
    ]
    assert any("echo: the mail the turn carries" in text for text in said), said
    assert registry.kill("s-orphan") is True


def test_headless_process_claude_retire_ladder_terminates_a_cli_that_ignores_eof(tmp_path: Path) -> None:
    """「手番の終わり = process の終わり」は EOF の作法に依らない: EOF で降りない CLI(替え玉の
    DOEFF_HEADLESS_STUB_IGNORE_EOF)は器の梯子(EOF の猶予 → SIGTERM)が降ろす。呼び手(読み手の thread・
    monitor の拍)は止まらない。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s-eof.events.jsonl")
    process = registry.spawn(
        "s-eof",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-eof"],
        str(tmp_path),
        _stub_env({"DOEFF_HEADLESS_STUB_IGNORE_EOF": "1"}),
        events,
        ClaudeDialogue(),
    )
    assert process.deliver("hello") is True
    _wait_until(lambda: process.retired)
    assert process.alive() is True  # EOF を無視して居座っている(梯子の猶予の中)
    assert process.observe().accepts_turn is False
    _wait_until(lambda: not process.alive(), timeout=EOF_GRACE_SECONDS + TERM_GRACE_SECONDS + 2.0)
    assert process.exit_code() != 0  # SIGTERM で降ろされた
    # 梯子の途中に次の手番が来ても、同じ名の登記は付き添い終えてから置き換わる(生きた process は 1 つ)
    assert registry.kill("s-eof") is True


def assistant_texts(records: list[JSONObject]) -> list[str]:
    """assistant の行の本文(text の block)を順に(検の読み口 — 形の合わない block は読まない)。"""
    texts: list[str] = []
    for record in records:
        if record.get("type") != "assistant":
            continue
        content = _obj(record, "message").get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
    return texts


def test_headless_process_claude_inject_reaches_the_running_turn(tmp_path: Path) -> None:
    """段 8 lane 4x: 走っている手番へ書いた user の行は、その手番の中で反応され(result の前)、
    手番は 1 つのまま終わる。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s3.events.jsonl")
    process = registry.spawn(
        "s3",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-3"],
        str(tmp_path),
        _stub_env({"DOEFF_HEADLESS_STUB_DELAY": "5"}),
        events,
        ClaudeDialogue(),
    )
    assert process.deliver("slow work") is True
    _wait_until(lambda: len(process.peek_records()) >= 4)
    assert process.inject("stop and answer") is True
    _wait_until(lambda: any(r.get("type") == "result" for r in process.peek_records()), timeout=10.0)
    _wait_until(lambda: not process.alive())  # 段 12 lane 12e(#517): 手番の終わり = process の終わり
    observed = process.observe()
    assert observed.alive is False
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    assert assistant_texts(list(observed.records)) == ["echo: slow work", "interrupted: stop and answer"]
    result = [r for r in observed.records if r.get("type") == "result"][-1]
    assert result["num_turns"] == 2
    # 手番が終わった後は引き受けない
    assert process.inject("too late") is False
    registry.kill("s3")


def test_headless_process_claude_escalate_stops_the_tool_and_the_injection_runs_next(tmp_path: Path) -> None:
    """段 10 lane 10n: 道具の途中(境界が来ない)に注入 → queued のまま → escalate(control_request interrupt)→ 替え玉は
    実物の順(control_response の still_queued → 止めた段の result (is_error) → started → init → 反応 → result)。
    器の観測は手番の終わり 1 つ(止めた段の result は飲む)・process は生きたまま。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s4.events.jsonl")
    process = registry.spawn(
        "s4",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-4"],
        str(tmp_path),
        _stub_env({"DOEFF_HEADLESS_STUB_DELAY": "30", "DOEFF_HEADLESS_STUB_TOOL_SECONDS": "30"}),
        events,
        ClaudeDialogue(),
    )
    assert process.escalate() is False  # 手番が走っていない
    assert process.deliver("long tool") is True
    _wait_until(lambda: len(process.peek_records()) >= 4)
    assert process.escalate() is False  # queued の注入が無い
    assert process.inject("stop and answer", "msg-1") is True
    _wait_until(lambda: any(r.get("type") == "command_lifecycle" and r.get("command_uuid") == "msg-1" for r in process.peek_records()))
    assert process.escalate() is True
    assert process.escalate() is False  # 答え待ち
    _wait_until(
        lambda: sum(1 for r in process.peek_records() if r.get("type") == "result") >= 2, timeout=10.0
    )
    _wait_until(lambda: any(r.get("type") == "command_lifecycle" and r.get("state") == "completed" and r.get("command_uuid") == "msg-1" for r in process.peek_records()), timeout=5.0)
    _wait_until(lambda: not process.alive())  # 段 12 lane 12e(#517): 手番の終わり = process の終わり
    observed = process.observe()
    assert observed.alive is False
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    kinds = [r["type"] for r in observed.records]
    assert kinds.count("result") == 2
    assert kinds.count("control_response") == 1
    assert kinds.index("control_response") < kinds.index("result")
    assert assistant_texts(list(observed.records)) == ["echo: long tool", "echo: stop and answer"]
    results = [r for r in observed.records if r.get("type") == "result"]
    assert results[0]["subtype"] == "error_during_execution"
    assert results[0]["is_error"] is True
    assert results[1]["subtype"] == "success"
    states = [(r["command_uuid"], r["state"]) for r in observed.records if r.get("type") == "command_lifecycle" and r.get("command_uuid") == "msg-1"]
    assert states == [("msg-1", "queued"), ("msg-1", "started"), ("msg-1", "completed")]
    # 手番が終わった後は引き受けない・出す物も無い
    assert process.inject("too late", "msg-2") is False
    assert process.escalate() is False
    registry.kill("s4")


def test_headless_process_claude_sigint_ends_the_turn_as_interrupted(tmp_path: Path) -> None:
    registry = HeadlessRegistry()
    events = str(tmp_path / "s2.events.jsonl")
    process = registry.spawn(
        "s2",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-2"],
        str(tmp_path),
        _stub_env({"DOEFF_HEADLESS_STUB_DELAY": "30"}),
        events,
        ClaudeDialogue(),
    )
    assert process.deliver("slow") is True
    _wait_until(lambda: len(process.observe().records) >= 1 or True)
    _pause(0.3)
    assert process.alive() is True
    assert process.interrupt() is True
    _wait_until(lambda: not process.alive(), timeout=10.0)
    observed = process.observe()
    assert observed.ended == ()
    assert observed.interrupted is True
    assert turn_verdict(observed, True).kind == "turn-ended"
    assert turn_verdict(observed, True).detail == "interrupted"
    registry.kill("s2")


def test_headless_process_codex_warm_turns_and_interrupt(tmp_path: Path) -> None:
    registry = HeadlessRegistry()
    events = str(tmp_path / "c1.events.jsonl")
    process = registry.spawn(
        "c1",
        ["codex", "app-server", "--listen", "stdio://"],
        str(tmp_path),
        _stub_env(),
        events,
        CodexDialogue(CodexPlan(cwd=str(tmp_path))),
    )
    assert process.deliver("first") is True
    _wait_until(lambda: bool(process.observe().ended))
    assert process.alive() is True
    observed = process.observe()  # 束は空になっている
    assert observed.ended == ()
    assert observed.accepts_turn is True
    assert process.dialogue.conversation == {"session_id": "thr-stub-1"}
    # 2 手番目は同じ process(温かい)
    assert process.deliver("second") is True
    _wait_until(lambda: bool(process.observe().ended))
    methods = [
        str(record.get("method"))
        for record in map(json.loads, Path(events).read_text(encoding="utf-8").splitlines())
        if "method" in record
    ]
    assert methods.count("turn/completed") == 2
    assert "item/agentMessage/delta" in methods
    registry.kill("c1")
    assert process.alive() is False


def test_headless_process_codex_interrupt_completes_as_interrupted(tmp_path: Path) -> None:
    registry = HeadlessRegistry()
    events = str(tmp_path / "c2.events.jsonl")
    process = registry.spawn(
        "c2",
        ["codex", "app-server", "--listen", "stdio://"],
        str(tmp_path),
        _stub_env({"DOEFF_HEADLESS_STUB_DELAY": "30"}),
        events,
        CodexDialogue(CodexPlan(cwd=str(tmp_path))),
    )
    assert process.deliver("slow") is True
    dialogue = process.dialogue
    assert isinstance(dialogue, CodexDialogue)
    _wait_until(lambda: dialogue.state.turn_id != "")
    assert process.interrupt() is True
    _wait_until(lambda: bool(process.observe().ended) or True)
    deadline = time.monotonic() + 5
    ended: tuple[TurnEnded, ...] = ()
    while time.monotonic() < deadline and not ended:
        ended = process.observe().ended
        _pause(0.05)
    assert ended == (TurnEnded(ok=False, detail="turn-interrupted"),)
    assert process.alive() is True
    registry.kill("c2")


#: 降りない替え玉(agora-redesign #547): stdin の EOF で降りず SIGTERM も無視する — 降ろせるのは SIGKILL だけ。
#: 無視の構えが済んでから 1 行名乗る(検はその行を待ち、構えより先に SIGTERM が届く競りを消す)。
_STUBBORN_STUB = """
import json, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
sys.stdout.write(json.dumps({"type": "stubborn-ready"}) + "\\n")
sys.stdout.flush()
while True:
    time.sleep(0.05)
"""


def _no_force_kill(_process: HeadlessProcess) -> None:
    """SIGKILL が効くまでの窓の再現 — 何も送らない。"""


@pytest.fixture
def spawn_stubborn(tmp_path: Path) -> Iterator[Callable[[HeadlessRegistry, str], HeadlessProcess]]:
    """降りない替え玉を起こす口。検が赤で抜けても替え玉を残さない(自分が起こした pid だけを SIGKILL)。"""
    spawned: list[HeadlessProcess] = []

    def spawn(registry: HeadlessRegistry, name: str) -> HeadlessProcess:
        process = registry.spawn(
            name,
            [sys.executable, "-c", _STUBBORN_STUB],
            str(tmp_path),
            dict(os.environ),
            str(tmp_path / f"{name}.events.jsonl"),
            ClaudeDialogue(),
        )
        spawned.append(process)
        _wait_until(lambda: len(process.peek_records()) >= 1)
        return process

    yield spawn
    for process in spawned:
        process.force_kill()


def test_headless_registry_kill_leaves_no_process_and_forgets_only_what_went_down(
    spawn_stubborn: Callable[[HeadlessRegistry, str], HeadlessProcess],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """agora-redesign #547: cleanup の後に同じ session の process が残らない。

    1. EOF も SIGTERM も効かない process は SIGKILL まで段を上げて降ろす(kill の戻りの時点で pid が無い)。
    2. 登記を忘れるのは**降りたのを確かめた後**: 猶予の中で降りなかった process(SIGKILL が効くまでの窓の
       再現 — force_kill を 1 度だけ空振りにする)は型付きに断り、登記に残す。旧形は降ろす前に登記から
       外したので、次の cleanup が「登記なし」と読んで cleaned_at を刻み、生きた process の持ち主が
       居なくなった(漏れ)。残した登記は次の cleanup が同じ process を降ろし直す。"""
    monkeypatch.setattr(headless_process, "EOF_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(headless_process, "TERM_GRACE_SECONDS", 0.2)
    registry = HeadlessRegistry()

    stubborn = spawn_stubborn(registry, "s-stubborn")
    assert registry.kill("s-stubborn") is True
    assert stubborn.exit_code() == -signal.SIGKILL
    assert headless_process.pid_exists(stubborn.pid) is False
    assert registry.get("s-stubborn") is None
    assert registry.kill("s-stubborn") is False  # 冪等: 降ろした登記はもう無い

    survivor = spawn_stubborn(registry, "s-survivor")
    with monkeypatch.context() as window:
        window.setattr(HeadlessProcess, "force_kill", _no_force_kill)
        with pytest.raises(HeadlessProcessStillAliveError) as refused:
            registry.kill("s-survivor")
    assert str(survivor.pid) in str(refused.value)
    assert headless_process.pid_exists(survivor.pid) is True
    # 所有を失わない: 同じ名の生きた登記のまま(同名の起こし直しも今日どおり拒む)
    assert registry.get("s-survivor") is survivor
    assert registry.has_alive("s-survivor") is True
    # 次の cleanup が同じ process を降ろし直す
    assert registry.kill("s-survivor") is True
    assert headless_process.pid_exists(survivor.pid) is False
    assert registry.get("s-survivor") is None


# ---------------------------------------------------------------- 3. host の RPC(backend=headless)


def _carries_turn_env(method: str, params: JSONObject) -> bool:
    """この呼びが process を起こす(= charter の env が子に届く)か。割り込みの送りは
    走っている手番へ本文を注ぐだけで process を起こさないので env を載せない(host が断る)。"""
    if method == "session.launch":
        return True
    return method == "session.send" and params.get("mode", "turn") == "turn"


class Host:
    """tmpdir の store + backend=headless の config(dispatch-line を直接叩く — socket なし)。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = host.parse_args(
            [
                "--db",
                str(root / "agentd.sqlite"),
                "--socket",
                str(root / "agentd.sock"),
                "--prompt-judge-cmd",
                "",
                "--backend",
                "headless",
                "serve",
            ]
        )
        self.actor = StoreActor(self.config.db_path)
        self._mut_counter = 0
        #: 替え玉の摘み(DOEFF_HEADLESS_STUB_*)は **charter の env** で運ぶ。段 10 lane 10d 便 2 の
        #: 追補 3 で、起こす process は agentd の process env を継がなくなった(名簿の外は届かない)—
        #: 検も本番と同じ路で摘みを渡す。値を替えると、その後に起こる process から効く
        #: (monkeypatch.setenv と同じ意味論: 走っている process の env は変わらない)。
        self.stub_env: dict[str, str] = {}

    def call(self, method: str, params: JSONObject) -> JSONObject:
        self._mut_counter += 1
        if self.stub_env and _carries_turn_env(method, params):
            declared = params.get("session_env")
            overlay: JSONObject = dict(declared) if isinstance(declared, dict) else {}
            params = {**params, "session_env": {**self.stub_env, **overlay}}
        line = json.dumps({"id": self._mut_counter, "method": method, "params": params})
        return _record(host.dispatch_line(line, self.config, self.actor))

    def ok(self, method: str, params: JSONObject) -> JSON:
        response = self.call(method, params)
        assert response["ok"] is True, response
        return response["result"]

    def snap(self, session_id: str) -> JSONObject:
        result = self.ok("session.get", {"session_id": session_id})
        assert isinstance(result, dict), result
        return result

    def monitor(self) -> JSONObject:
        outcomes = host.run_hosted(self.config, self.actor, headless_hy.headless_monitor_cycle())
        assert isinstance(outcomes, dict)
        return {str(key): str(value) for key, value in outcomes.items()}

    def close(self) -> None:
        self.actor.close()


@pytest.fixture
def headless_host(monkeypatch: pytest.MonkeyPatch) -> Iterator[Host]:
    root = Path(tempfile.mkdtemp(prefix="doeff-headless-"))
    monkeypatch.setenv("PATH", f"{STUBS}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("DOEFF_SESSIONHOST_HEADLESS_DIR", str(root / "events"))
    monkeypatch.delenv("DOEFF_HEADLESS_STUB_DELAY", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    # 器の登記簿(host.HEADLESS_REGISTRY)は module の大域 — 検ごとに新しい簿を持たせる。共有のままだと前の検が
    # 残した process が次の検の「登記の全 process」(停止の腕の killed の数・復帰の所有の観測)に混ざり、package を
    # 通しで走らせた時だけ数が合わない(2026-09-24 zeus / Mac: killed 7 != 2)。後始末で簿に残った process を降ろす。
    registry = HeadlessRegistry()
    monkeypatch.setattr(host, "HEADLESS_REGISTRY", registry)
    host_under_test = Host(root)
    try:
        yield host_under_test
    finally:
        host.HEADLESS_REGISTRY.kill_all()
        registry.kill_all()
        host_under_test.close()
        shutil.rmtree(root, ignore_errors=True)


def _launch_params(
    root: Path, sid: str, agent_type: str, lifecycle: str = "multi_turn"
) -> JSONObject:
    work = root / "work"
    work.mkdir(exist_ok=True)
    binding: JSONObject = (
        {"kind": "claude-code", "config_dir": str(root / "claude-home")}
        if agent_type == "claude"
        else {"kind": "codex", "codex_home": str(root / "codex-home")}
    )
    return {
        "session_id": sid,
        "session_name": sid,
        "agent_type": agent_type,
        "work_dir": str(work),
        "lifecycle": lifecycle,
        "prompt": "hello agent",
        "binding": binding,
        "model": "stub-model",
    }


def _wait_turn_end(headless_host: Host, sid: str) -> JSONObject:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        headless_host.monitor()
        snap = headless_host.snap(sid)
        if _has(snap, "turn_ended_at") or snap["status"] != "running":
            return snap
        _pause(0.05)
    raise AssertionError("turn did not end")


def test_host_headless_turn_refused_by_the_provider_limit_fails_the_session_with_the_cause(
    headless_host: Host,
) -> None:
    """段 11 lane 11n 便 C(agora-redesign #179・依頼者の裁定 2026-09-15 案 c′): CLI が限度で断った
    手番は、温かい session を残さず **器ごと** 終端(status failed・cause rate_limited)にする。

    族の表は impls/markers.hy の 1 点(pane の路と同じ表)で、当てるのは headless.hy の手番の腕の
    1 点(headless-turn-limit-cause)。制御面(agentd)はこの欄を読んで agent-job に条件
    ProviderLimit を刻む —— 実弾 2026-09-15 13:2x では器が running のまま残り、行に何も残らず、
    同じ profile の次の手番も同じ限度で断られ続けた(5 連敗)。
    """
    limit_text = "You've reached your Fable limit. /model to switch models."
    headless_host.stub_env["DOEFF_HEADLESS_STUB_LIMIT_TEXT"] = limit_text
    launched = headless_host.ok(
        "session.launch", _launch_params(headless_host.root, "h-limit", "claude")
    )
    assert isinstance(launched, dict)
    assert _text(launched, "status") == "running"
    ended = _wait_turn_end(headless_host, "h-limit")
    # 温かいままにしない: 行は failed で、cause は rate_limited(理由は CLI の文そのまま)
    assert _text(ended, "status") == "failed", ended
    cause = _obj(ended, "terminal_cause")
    assert _text(cause, "category") == "rate_limited", cause
    assert limit_text in _text(cause, "reason"), cause
    # 2026-09-23: 文が model の族(Fable)を名乗る断りだけが limit_scope = model(器の側が当てて欄に載せる — R33)
    assert _text(cause, "limit_scope") == "model", cause
    # 2026-09-24(card acp:kanban-issue:ki-5d4849d22a4e): 範囲とは別の軸の理由(今日は rate-limited の 1 語)
    assert _text(cause, "limit_reason") == "rate-limited", cause
    assert ended["awaiting_response"] is False
    # 手番の終わりの印も立つ(手番は終わっている — level-triggered の欄)
    assert _has(ended, "turn_ended_at")
    # 限度でない普通の手番は今日どおり温かい(同じ腕が二重に効かない)
    headless_host.stub_env.pop("DOEFF_HEADLESS_STUB_LIMIT_TEXT")
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-warm", "claude"))
    warm = _wait_turn_end(headless_host, "h-warm")
    assert _text(warm, "status") == "running", warm
    assert not _has(warm, "terminal_cause")
    # 器を片付ける(登記簿は module をまたぐので、残した process は他の検の数を狂わせる)
    for sid in ("h-limit", "h-warm"):
        headless_host.ok("session.cleanup", {"session_id": sid})


def test_host_headless_turn_refused_with_api_status_429_is_a_limit_whatever_the_wording(
    headless_host: Host,
) -> None:
    """agora-redesign #513(operator 指示 2026-09-17 "hitting that limit message must automatically
    switch profile"): 限度の断りは **CLI が構造で名乗る status(429)** で当てる —— 文の言い回しに依らない。

    実弾 2026-09-17: 会社の口座 p10174 の 18 手番が「Your group's usage limit is set to $0 · ask your
    admin for a higher limit」で断られたが、文が所有格族に当たらず、器は温かいまま・agent-job は
    completed で終わり、予算の係へ 1 bit も届かず、配置は同じ口座に結び続けた。どの文も result の行は
    subtype success・is_error・terminal_reason api_error・api_error_status 429 だった。
    429 でない status(403 = 組織の剥奪 など)は限度ではない —— 器は今日どおり温かい。
    """
    # 族の表に無い言い回し + 429 → 限度(器ごと終端・cause rate_limited・理由は CLI の文そのまま)
    unknown_wording = "Usage is paused for this workspace · ask your admin"
    headless_host.stub_env["DOEFF_HEADLESS_STUB_LIMIT_TEXT"] = unknown_wording
    headless_host.stub_env["DOEFF_HEADLESS_STUB_API_ERROR_STATUS"] = "429"
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-429", "claude"))
    ended = _wait_turn_end(headless_host, "h-429")
    assert _text(ended, "status") == "failed", ended
    cause = _obj(ended, "terminal_cause")
    assert _text(cause, "category") == "rate_limited", cause
    assert unknown_wording in _text(cause, "reason"), cause
    # 2026-09-23: model を名乗らない断りは口座全体(operator の規則「種類を問わず口座が枯れた」)
    assert _text(cause, "limit_scope") == "account", cause
    # limit の語を含む文でも 403 は限度ではない(構造が先 — 文は読まない)
    headless_host.stub_env["DOEFF_HEADLESS_STUB_LIMIT_TEXT"] = (
        "Your organization has disabled Claude subscription access · usage limit reached"
    )
    headless_host.stub_env["DOEFF_HEADLESS_STUB_API_ERROR_STATUS"] = "403"
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-403", "claude"))
    other = _wait_turn_end(headless_host, "h-403")
    assert _text(other, "status") == "running", other
    assert not _has(other, "terminal_cause")
    headless_host.stub_env.pop("DOEFF_HEADLESS_STUB_LIMIT_TEXT")
    headless_host.stub_env.pop("DOEFF_HEADLESS_STUB_API_ERROR_STATUS")
    for sid in ("h-429", "h-403"):
        headless_host.ok("session.cleanup", {"session_id": sid})


def test_host_headless_turn_refused_by_the_group_usage_limit_leaves_the_range_unknown(
    headless_host: Host,
) -> None:
    """card acp:kanban-issue:ki-5d4849d22a4e(2026-09-24・受入 A6): 「Your group's usage limit is set to $N」の
    族は、どの窓が枯れたかも model も名乗らない —— 器は範囲を決めず、cause の limit_scope に unknown を
    名乗る(範囲と理由の推定は窓の写しを持つ予算の係の 1 点 — 契約 ACP scheduling.json
    providerRefusal.unknownScope)。範囲を名乗る文(session の限度)は今日どおり account。記録の 2 軸目
    limit_reason は今日どちらも rate-limited(文が名乗る理由 — 範囲とは別の軸)。

    実物の文 = 2026-09-23 15:33〜16:33 JST に会社の口座 7 枚で Fable の手番が受けた断りと、同じ拍に p10184 の
    Opus の手番が受けた session の限度の文。stub は 429 を名乗らせる(2026-09-17 の実測で限度の断りは文を問わず
    全部 429 — 構造が先・文が後)。
    """
    cases = (
        (
            "h-group-cap",
            "Your group's usage limit is set to $0 · ask your admin for a higher limit",
            "unknown",
        ),
        ("h-session", "You've hit your session limit · resets 7:10pm (Asia/Tokyo)", "account"),
    )
    headless_host.stub_env["DOEFF_HEADLESS_STUB_API_ERROR_STATUS"] = "429"
    for sid, said, scope in cases:
        headless_host.stub_env["DOEFF_HEADLESS_STUB_LIMIT_TEXT"] = said
        headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
        ended = _wait_turn_end(headless_host, sid)
        assert _text(ended, "status") == "failed", ended
        cause = _obj(ended, "terminal_cause")
        assert _text(cause, "category") == "rate_limited", cause
        assert said in _text(cause, "reason"), cause
        assert _text(cause, "limit_scope") == scope, cause
        assert _text(cause, "limit_reason") == "rate-limited", cause
    headless_host.stub_env.pop("DOEFF_HEADLESS_STUB_LIMIT_TEXT")
    headless_host.stub_env.pop("DOEFF_HEADLESS_STUB_API_ERROR_STATUS")
    for sid, _said, _scope in cases:
        headless_host.ok("session.cleanup", {"session_id": sid})


def test_host_headless_warm_turn_that_failed_keeps_the_runners_words_on_the_row(
    headless_host: Host,
) -> None:
    """依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(D2): 温かい session(multi_turn)の手番が限度でない失敗で終わった → 器は今日どおり
    温かいまま(status running・terminal_cause 無し)、行の turn_error に走行器が名乗った文(旧形は ok = False を provider の
    限度以外すべて捨て、行に 1 bit も残らなかった)。次の手番の送りで欄ごと消え(turn_ended_at と対の level-triggered)、
    成功で終わった手番は欄を持たない。"""
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-fine", "claude"))
    fine = _wait_turn_end(headless_host, "h-fine")
    assert _has(fine, "turn_ended_at")
    assert not _has(fine, "turn_error")
    said = "the CLI gave up before calling the model"
    headless_host.stub_env["DOEFF_HEADLESS_STUB_LIMIT_TEXT"] = said
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-err", "claude"))
    ended = _wait_turn_end(headless_host, "h-err")
    headless_host.stub_env.pop("DOEFF_HEADLESS_STUB_LIMIT_TEXT")
    assert _text(ended, "status") == "running", ended
    assert not _has(ended, "terminal_cause")
    assert _text(ended, "turn_error") == said
    headless_host.ok("session.send", {"session_id": "h-err", "message": "again", "awaiting": True})
    assert not _has(headless_host.snap("h-err"), "turn_error")
    _wait_turn_end(headless_host, "h-err")
    for sid in ("h-fine", "h-err"):
        headless_host.ok("session.cleanup", {"session_id": sid})


def test_host_headless_resumed_turn_runs_the_mail_past_the_clis_own_turn(
    headless_host: Host,
) -> None:
    """依頼 lt-R79KYTYMJH4ZT9X4KHWKCD23KB(実弾 2026-09-19 07:28 JST・pod j4bz4): 2 手番目の ``--resume`` で起きた CLI が本文より
    先に孤児の task の報せを自分の手番として走らせても(替え玉 DOEFF_HEADLESS_STUB_ORPHAN_NOTICES)、器の手番の終わりは本文の
    手番の result ちょうど — 本文への応答が events に在り、失敗の文は無い。旧形は CLI 自身の result で手番を閉じ、本文の手番を
    切った(出力 0 件の手番が completed を名乗り、郵便が黙って消費された)。"""
    headless_host.stub_env["DOEFF_HEADLESS_STUB_ORPHAN_NOTICES"] = "2"
    launched = headless_host.ok(
        "session.launch", _launch_params(headless_host.root, "h-orphan", "claude")
    )
    assert isinstance(launched, dict)
    events_path = Path(_text(_obj(launched, "backend_ref"), "events_path"))
    _wait_turn_end(headless_host, "h-orphan")
    first_turn = len(events_path.read_text(encoding="utf-8").splitlines())
    _pause(0.3)
    headless_host.ok(
        "session.send", {"session_id": "h-orphan", "message": "the second mail", "awaiting": True}
    )
    ended = _wait_turn_end(headless_host, "h-orphan")
    headless_host.stub_env.pop("DOEFF_HEADLESS_STUB_ORPHAN_NOTICES")
    assert _text(ended, "status") == "running", ended
    assert not _has(ended, "turn_error")
    _wait_until(
        lambda: not headless_process.pid_exists(int(str(_obj(ended, "backend_ref")["pid"]))),
        timeout=5.0,
    )
    second = [
        _record(line) for line in events_path.read_text(encoding="utf-8").splitlines()[first_turn:]
    ]
    results = [record for record in second if record.get("type") == "result"]
    assert [record.get("origin") for record in results] == [{"kind": "task-notification"}, None]
    said = [
        json.dumps(record.get("message")) for record in second if record.get("type") == "assistant"
    ]
    assert any("echo: the second mail" in text for text in said), said
    headless_host.ok("session.cleanup", {"session_id": "h-orphan"})


def test_host_headless_claude_round_trip_launch_turn_end_send_resume_cleanup(
    headless_host: Host,
) -> None:
    # 段 12 lane 12e(agora-redesign #517): 手番の終わり = process の終わり。器は result の行で stdin に EOF を出し、
    # 2 手番目からは毎回 --resume の新しい process(温かい同じ process への send は退役)。
    launched = headless_host.ok(
        "session.launch", _launch_params(headless_host.root, "h-1", "claude")
    )
    assert isinstance(launched, dict)
    assert _text(launched, "backend_kind") == "headless"
    assert _text(launched, "status") == "running"
    assert launched["awaiting_response"] is True
    events_path = Path(_text(_obj(launched, "backend_ref"), "events_path"))
    assert events_path.name == "h-1.events.jsonl"
    conversation = _text(_obj(launched, "conversation"), "session_id")
    assert conversation
    ended = _wait_turn_end(headless_host, "h-1")
    assert _text(ended, "status") == "running"
    assert ended["awaiting_response"] is False
    assert _has(ended, "turn_ended_at")
    first_turn = events_path.read_text(encoding="utf-8").splitlines()
    assert _record(first_turn[-1])["type"] == "result"
    assert _record(first_turn[0])["session_id"] == conversation
    # capture = events file の末尾
    captured = headless_host.ok("session.capture", {"session_id": "h-1", "lines": 2})
    assert _record(_text(captured, "text").splitlines()[-1])["type"] == "result"
    # 手番の終わりで process は降りている(retire)— monitor は降りた process を idle と読む(failed にしない)
    pid_before = _obj(ended, "backend_ref")["pid"]
    _pause(0.3)
    headless_host.monitor()
    assert headless_host.snap("h-1")["status"] == "running"
    # 次の手番: --resume の新しい process(同じ session の名・events file は同じ path に追記)— awaiting が立って
    # turn_ended_at が消える
    headless_host.ok(
        "session.send", {"session_id": "h-1", "message": "second turn", "awaiting": True}
    )
    after_send = headless_host.snap("h-1")
    assert after_send["awaiting_response"] is True
    assert not _has(after_send, "turn_ended_at")
    assert _obj(after_send, "backend_ref")["pid"] != pid_before
    assert "--resume" in _texts(_obj(after_send, "backend_ref"), "argv")
    ended_again = _wait_turn_end(headless_host, "h-1")
    assert _text(ended_again, "status") == "running"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    assert _record(lines[5])["resumed"] is True  # 替え玉は --resume の process で resumed を名乗る
    # 手番の外の割り込みは型付きに断る(新しい手番を起こさない)
    refused = headless_host.call(
        "session.send", {"session_id": "h-1", "message": "nothing runs", "mode": "interrupt"}
    )
    assert refused["ok"] is False, refused
    assert "no turn" in json.dumps(refused)
    # 語彙の外の mode は断る
    bad_mode = headless_host.call(
        "session.send", {"session_id": "h-1", "message": "x", "mode": "shout"}
    )
    assert bad_mode["ok"] is False, bad_mode
    # 3 手番目も同じ: 降りた process の次の手番は --resume の process を起こし直す(毎手番)。
    pid_second = _obj(after_send, "backend_ref")["pid"]
    _pause(0.3)
    headless_host.monitor()
    assert headless_host.snap("h-1")["status"] == "running"
    headless_host.ok(
        "session.send", {"session_id": "h-1", "message": "third turn", "awaiting": True}
    )
    after_resume = headless_host.snap("h-1")
    assert "--resume" in _texts(_obj(after_resume, "backend_ref"), "argv")
    assert _obj(after_resume, "backend_ref")["pid"] not in (pid_before, pid_second)
    ended_third = _wait_turn_end(headless_host, "h-1")
    assert _text(ended_third, "status") == "running"
    assert len(events_path.read_text(encoding="utf-8").splitlines()) == 15
    # cleanup = 終端
    cleaned = headless_host.ok("session.cleanup", {"session_id": "h-1"})
    assert _text(cleaned, "status") == "stopped"
    assert _has(cleaned, "cleaned_at")


def test_host_headless_claude_interrupt_mode_reaches_the_running_turn(
    headless_host: Host,
) -> None:
    """段 8 lane 4x: session.send の mode = interrupt は走っている手番へ本文を注入する — 手番は
    1 つのまま(awaiting は触らない)、反応が実況(events)に出てから result。"""
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "5"
    launched = headless_host.ok("session.launch", _launch_params(headless_host.root, "h-5", "claude"))
    assert isinstance(launched, dict)
    events_path = Path(_text(_obj(launched, "backend_ref"), "events_path"))
    _wait_until(lambda: len(events_path.read_text(encoding="utf-8").splitlines()) >= 4 if events_path.exists() else False)
    injected = headless_host.ok(
        "session.send", {"session_id": "h-5", "message": "change of plan", "mode": "interrupt"}
    )
    assert isinstance(injected, dict)
    assert injected["sent"] is True
    still = headless_host.snap("h-5")
    assert still["awaiting_response"] is True
    assert not _has(still, "turn_ended_at")
    ended = _wait_turn_end(headless_host, "h-5")
    assert _text(ended, "status") == "running"
    lines = [_record(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert assistant_texts(lines) == ["echo: hello agent", "interrupted: change of plan"]
    assert [r["type"] for r in lines].count("result") == 1
    headless_host.ok("session.cleanup", {"session_id": "h-5"})


def test_host_headless_escalate_stops_the_turn_and_keeps_awaiting(
    headless_host: Host,
) -> None:
    """段 10 lane 10n: session.send(mode = interrupt・ref)の後の session.escalate は停止の合図を出す —
    行の awaiting は触らない(host から見た手番は続く)。出す物が無い時(queued の注入が無い・既に出した)は
    型付きに断る。手番の終わりは注入の行の手番の result で 1 つ。"""
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "30"
    headless_host.stub_env["DOEFF_HEADLESS_STUB_TOOL_SECONDS"] = "30"
    launched = headless_host.ok("session.launch", _launch_params(headless_host.root, "h-7", "claude"))
    assert isinstance(launched, dict)
    events_path = Path(_text(_obj(launched, "backend_ref"), "events_path"))
    _wait_until(lambda: len(events_path.read_text(encoding="utf-8").splitlines()) >= 4 if events_path.exists() else False)
    refused = headless_host.call("session.escalate", {"session_id": "h-7"})
    assert refused["ok"] is False
    assert "nothing to escalate" in str(refused["error"])
    injected = headless_host.ok(
        "session.send", {"session_id": "h-7", "message": "change of plan", "mode": "interrupt", "ref": "msg-7"}
    )
    assert isinstance(injected, dict)
    assert injected["sent"] is True
    _wait_until(
        lambda: any(
            _record(line).get("command_uuid") == "msg-7"
            for line in events_path.read_text(encoding="utf-8").splitlines()
        )
    )
    escalated = headless_host.ok("session.escalate", {"session_id": "h-7"})
    assert isinstance(escalated, dict)
    assert _text(escalated, "status") == "running"
    assert escalated["awaiting_response"] is True
    again = headless_host.call("session.escalate", {"session_id": "h-7"})
    assert again["ok"] is False
    ended = _wait_turn_end(headless_host, "h-7")
    assert _text(ended, "status") == "running"
    lines = [_record(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert assistant_texts(lines) == ["echo: hello agent", "echo: change of plan"]
    assert [r["type"] for r in lines].count("result") == 2
    assert [r["type"] for r in lines].count("control_response") == 1
    headless_host.stub_env["DOEFF_HEADLESS_STUB_TOOL_SECONDS"] = "0"
    headless_host.ok("session.cleanup", {"session_id": "h-7"})


def test_host_headless_interrupt_keeps_the_session_warm(
    headless_host: Host,
) -> None:
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "30"
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-2", "claude"))
    _pause(0.3)
    headless_host.monitor()
    running = headless_host.snap("h-2")
    assert running["awaiting_response"] is True
    interrupted = headless_host.ok("session.interrupt", {"session_id": "h-2"})
    assert _text(interrupted, "status") == "running"
    ended = _wait_turn_end(headless_host, "h-2")
    assert _text(ended, "status") == "running"
    assert _has(ended, "turn_ended_at")
    assert ended["awaiting_response"] is False
    # 温かいまま次の手番を受ける
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "0"
    headless_host.ok(
        "session.send", {"session_id": "h-2", "message": "after interrupt", "awaiting": True}
    )
    again = _wait_turn_end(headless_host, "h-2")
    assert _text(again, "status") == "running"
    headless_host.ok("session.cancel", {"session_id": "h-2"})
    assert _text(headless_host.snap("h-2"), "status") == "stopped"


def _digest(token: str) -> str:
    """替え玉が名乗る資格の指紋と同じ綴り(検も値そのものは持ち回らない)。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _init_records(events_path: Path) -> list[JSONObject]:
    """events の init の行(= process が起きるたびに 1 行 — 何番目の process が何を名乗ったか)。"""
    return [
        record
        for line in events_path.read_text(encoding="utf-8").splitlines()
        for record in [_record(line)]
        if record.get("subtype") == "init"
    ]


def _init_digests(events_path: Path) -> list[str]:
    """events の init の行が名乗った資格の指紋を順に(= 何番目の process がどの札で起きたか)。"""
    return [str(record.get("auth_digest", "")) for record in _init_records(events_path)]


def test_host_headless_resume_starts_with_the_token_the_turn_carries(headless_host: Host) -> None:
    """段 10 lane 10d 便 2 の追補 2(実弾 #92 — 預かり所が口座を更新した拍に、温かい session の
    再開の手番が誕生時の access token を使い回して 401 revoked)。降りた process の起こし直しは
    **その手番の送りが運ぶ env** で起き、行には札を残さない(検は指紋だけを読む)。"""
    headless_host.stub_env["DOEFF_HEADLESS_STUB_TURNS_BEFORE_EXIT"] = "1"
    params = _launch_params(headless_host.root, "h-9", "claude")
    params["session_env"] = {"CLAUDE_CODE_OAUTH_TOKEN": "tok-birth"}
    launched = headless_host.ok("session.launch", params)
    assert isinstance(launched, dict)
    events_path = Path(_text(_obj(launched, "backend_ref"), "events_path"))
    # 行に残る launch の意図(再開の材料)から手番ごとの札は落ちている — 非 auth の宣言は残る
    overlay = _obj(_obj(launched, "launch_overlay"), "session_env")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in overlay
    assert overlay["DOEFF_HEADLESS_STUB_TURNS_BEFORE_EXIT"] == "1"
    # 誕生の process はその手番の札(= launch の charter の札)で起きた
    _wait_until(lambda: _init_digests(events_path) == [_digest("tok-birth")])
    _wait_turn_end(headless_host, "h-9")
    # 替え玉は 1 手番目の result の後に降りる(idle で退いた器の再現)
    _wait_until(lambda: headless_host.snap("h-9")["backend_alive"] is False)
    pid_before = _obj(headless_host.snap("h-9"), "backend_ref")["pid"]
    # 次の手番は新しい札で来る(貸与が回った拍)— 起こし直しはこの札で起きる
    headless_host.ok(
        "session.send",
        {
            "session_id": "h-9",
            "message": "second turn",
            "awaiting": True,
            "session_env": {"CLAUDE_CODE_OAUTH_TOKEN": "tok-turn2"},
        },
    )
    after = headless_host.snap("h-9")
    assert "--resume" in _texts(_obj(after, "backend_ref"), "argv")
    assert _obj(after, "backend_ref")["pid"] != pid_before
    # 起こし直した process の init は非同期に書かれる — 指紋が 2 つ並ぶまで待つ
    _wait_until(
        lambda: _init_digests(events_path) == [_digest("tok-birth"), _digest("tok-turn2")]
    )
    # 行は札を持たないまま(手番が運んだ札も残さない)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in _obj(_obj(after, "launch_overlay"), "session_env")
    _wait_turn_end(headless_host, "h-9")
    headless_host.ok("session.cleanup", {"session_id": "h-9"})


def test_host_headless_send_refuses_the_env_it_cannot_carry(headless_host: Host) -> None:
    """手番ごとの env の関所は launch と同じ 1 点(binding 所有キー・従量課金 credential は
    送りの口でも受けない)。運べない組み合わせ(割り込みの送り)は黙って落とさず断る —
    落とすと誕生の札で手番が走る(実弾 #92 の形)。"""
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-10", "claude"))
    _wait_turn_end(headless_host, "h-10")
    binding_owned = headless_host.call(
        "session.send",
        {"session_id": "h-10", "message": "x", "session_env": {"CLAUDE_CONFIG_DIR": "/tmp/elsewhere"}},
    )
    assert binding_owned["ok"] is False
    assert "non-auth overlay" in json.dumps(binding_owned)
    metered = headless_host.call(
        "session.send",
        {"session_id": "h-10", "message": "x", "session_env": {"ANTHROPIC_AUTH_TOKEN": "k"}},
    )
    assert metered["ok"] is False
    assert "metered-billing credentials are forbidden" in json.dumps(metered)
    interrupting = headless_host.call(
        "session.send",
        {
            "session_id": "h-10",
            "message": "x",
            "mode": "interrupt",
            "session_env": {"CLAUDE_CODE_OAUTH_TOKEN": "tok"},
        },
    )
    assert interrupting["ok"] is False
    assert "starts no process" in json.dumps(interrupting)
    headless_host.ok("session.cleanup", {"session_id": "h-10"})


def test_the_spawned_turn_inherits_no_agentd_env(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段 10 lane 10d 便 2 の追補 3(実弾 #95): 手番の CLI は agentd の env を継がない。
    機体から継ぐのは基本の名簿だけで、会話ごとの値は charter が運ぶ — 起こした process の
    env に ACP_ / DOEFF_ の鍵は 0(charter が運んだ摘みを除く)、預かり所・記録・借り手札の
    宛先も届かない。⚠ 検は**名の在否**だけを読む(値は 1 つも持ち回らない)。"""
    for name, value in {
        "ACP_BASE_URL": "https://acp.example",
        "ACP_AGENTD_TOKEN_FILE": "/run/secrets/acp-token",
        "DOEFF_AGENTD_NODE_NAME": "mac",
        "AGORA_BORROWER_KEY_PATH": "/run/secrets/borrower",
        "AGORA_CUSTODY_URL": "https://custodian.example",
        "RECORD_SERVICE_URL": "https://record.example",
    }.items():
        monkeypatch.setenv(name, value)
    params = _launch_params(headless_host.root, "h-11", "claude")
    params["session_env"] = {"AGORA_CONVERSATION_ID": "c-1", "AGORA_SEAT_OPENER": "machine"}
    launched = headless_host.ok("session.launch", params)
    assert isinstance(launched, dict)
    events_path = Path(_text(_obj(launched, "backend_ref"), "events_path"))
    _wait_until(lambda: bool(_init_records(events_path)))
    seen = _texts(_init_records(events_path)[0], "system_env_names")
    # charter が運んだ名だけが子に居る(摘みは Host の harness が charter へ載せる)
    assert sorted(seen) == sorted(
        ["AGORA_CONVERSATION_ID", "AGORA_SEAT_OPENER"]
        + list(headless_host.stub_env)
    ), f"手番の CLI が agentd の env を継いでいる: {seen}"
    assert not [name for name in seen if name.startswith("ACP_")]
    for dropped in ("AGORA_BORROWER_KEY_PATH", "AGORA_CUSTODY_URL", "RECORD_SERVICE_URL"):
        assert dropped not in seen
    _wait_turn_end(headless_host, "h-11")
    headless_host.ok("session.cleanup", {"session_id": "h-11"})


def _seed_claude_transcript(host: Host, launched: JSONObject) -> None:
    """resume の admission が要る transcript(実物の CLI が書く projects/<mangled cwd>/<conv>.jsonl — 替え玉は書かない)を置く。"""
    conversation = _text(_obj(launched, "conversation"), "session_id")
    mangled = "".join(ch if ch.isalnum() else "-" for ch in os.path.realpath(_text(launched, "work_dir")))
    transcript = host.root / "claude-home" / "projects" / mangled / f"{conversation}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    if not transcript.exists():
        transcript.write_text("{}\n", encoding="utf-8")


def test_the_seat_reads_the_record_destination_of_the_agentd_that_woke_it(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """card acp:kanban-issue:ki-e930b8506201 C1 / C5(K1'): 起こした process の env の RECORD_SERVICE_URL は、その手番を起こした
    agentd の記録の宛先(参加の門を通った値)と byte 同一。agentd の判断(node-seat-env-of → session-attribution-of →
    incarnation-charter-of → next-arm-for-job)を本物のまま撃ち、本物の host(backend=headless)が替え玉の CLI を起こす。
    - launch と、同じ agentd の次の手番(send → 降りた process の続き — 行に保存した生まれた時の env)は A の値。
    - 手番の間に agentd を宛先 B で起こし直すと、腕は send ではなく resume(候補を片付けて同じ家で --resume)で、
      起こした process は B の値(盲検 A・B の反例: 送りを選ぶと続きの process は行の A を再生する)。
    - 差し替えない agentd B の次の手番は send で B のまま(cache を捨てない)。
    - 記録が無効な agentd の席には名が無く、機体の env の値も継がない(R30 (4))。"""
    from dataclasses import replace

    from doeff_agents.sessionhost.acp import judgment
    from doeff_agents.sessionhost.acp.effects import (
        RECORD_URL_ENV,
        AgentdSettings,
        ArmChoice,
        LaunchPlan,
    )
    from doeff_agents.sessionhost.acp.handlers import session_view_of

    monkeypatch.setenv(RECORD_URL_ENV, "http://machine-env.invalid:1")
    url_a, url_b = "http://record-a.example:8874", "http://record-b.example:8874"
    declared = _launch_params(headless_host.root, "unused", "claude")
    charter: JSONObject = {k: v for k, v in declared.items() if k not in ("session_id", "session_name")}
    charter["session_env"] = {"DOEFF_HEADLESS_STUB_ECHO_ENV": RECORD_URL_ENV}
    plan = LaunchPlan(
        charter=charter, predecessor=None, lease_kind=None, account=None, profile="personal-1", model="stub-model"
    )
    home = run(judgment.session_affinity_key_of(plan))
    settings_a = AgentdSettings(node_name="mac-1", record_url=url_a, seat_env=(("ACP_BASE", "http://acp:8868"),))
    settings_b = replace(settings_a, record_url=url_b)

    def woken(settings: AgentdSettings, arm: str, sid: str, source: str | None = None) -> JSONObject:
        node_env = run(judgment.node_seat_env_of(settings))
        attribution = run(judgment.session_attribution_of(plan, "aj-1", "c-1", arm, node_env))
        built = run(
            judgment.incarnation_charter_of(
                plan, ArmChoice(arm=arm, source=source, retire=None), sid, "", (), "", attribution, "headless",
                None, str(headless_host.root / "homes"), "", "operator", node_env,
            )
        )
        assert isinstance(built, tuple)
        woke = built[0]
        assert isinstance(woke, dict)
        return woke

    def next_arm(sid: str, settings: AgentdSettings) -> object:
        view = session_view_of(headless_host.snap(sid))
        digest = run(judgment.node_seat_env_digest_of(run(judgment.node_seat_env_of(settings))))
        return run(judgment.next_arm_for_job(sid, view, home, None, False, digest))

    def echoed(events_path: Path) -> list[object]:
        return [_obj(record, "echoed_env").get(RECORD_URL_ENV) for record in _init_records(events_path)]

    def turn_over(sid: str) -> None:
        _wait_turn_end(headless_host, sid)
        _wait_until(lambda: headless_host.snap(sid)["backend_alive"] is False)

    def send(sid: str, message: str) -> None:
        headless_host.ok(
            "session.send",
            {"session_id": sid, "message": message, "awaiting": True, "session_env": run(judgment.turn_session_env_of(None))},
        )

    # agentd A が起こす(launch)→ A の値
    launched = headless_host.ok("session.launch", woken(settings_a, "launch", "k1-a"))
    assert isinstance(launched, dict)
    events_a = Path(_text(_obj(launched, "backend_ref"), "events_path"))
    _wait_until(lambda: echoed(events_a) == [url_a])
    turn_over("k1-a")
    # 同じ agentd A の次の手番: send(降りた process の続き)→ 行の生まれた時の env = A
    assert next_arm("k1-a", settings_a) == ArmChoice("send", "k1-a", None)
    send("k1-a", "second turn")
    _wait_until(lambda: echoed(events_a) == [url_a, url_a])
    turn_over("k1-a")
    # agentd を B で起こし直した後の手番: send を選ばず resume → 起こした process は B
    choice = next_arm("k1-a", settings_b)
    assert choice == ArmChoice("resume", "k1-a", "k1-a")
    headless_host.ok("session.cleanup", {"session_id": "k1-a"})
    _seed_claude_transcript(headless_host, launched)
    resumed = headless_host.ok(
        "session.resume", run(judgment.resume_params_of("k1-a", woken(settings_b, "resume", "k1-b", "k1-a")))
    )
    assert isinstance(resumed, dict)
    events_b = Path(_text(_obj(resumed, "backend_ref"), "events_path"))
    _wait_until(lambda: echoed(events_b) == [url_b])
    turn_over("k1-b")
    # 差し替えない agentd B の次の手番は send のまま(cache を捨てない)で B
    assert next_arm("k1-b", settings_b) == ArmChoice("send", "k1-b", None)
    send("k1-b", "third turn")
    _wait_until(lambda: echoed(events_b) == [url_b, url_b])
    turn_over("k1-b")
    headless_host.ok("session.cleanup", {"session_id": "k1-b"})
    # 記録が無効な agentd(試験の対照だけ)の席には名が無い — 機体の env の値も継がない
    off = headless_host.ok("session.launch", woken(replace(settings_a, record_url=None), "launch", "k1-off"))
    assert isinstance(off, dict)
    events_off = Path(_text(_obj(off, "backend_ref"), "events_path"))
    _wait_until(lambda: echoed(events_off) == [None])
    turn_over("k1-off")
    headless_host.ok("session.cleanup", {"session_id": "k1-off"})


def test_host_headless_codex_round_trip_is_warm(headless_host: Host) -> None:
    launched = headless_host.ok(
        "session.launch", _launch_params(headless_host.root, "h-3", "codex")
    )
    assert isinstance(launched, dict)
    assert _text(launched, "backend_kind") == "headless"
    assert not _has(launched, "conversation")  # codex の thread の id は応答で知る
    # 替え玉の codex は router shim と同じ綴りを拒む(tests/headless_stubs/codex)— 起こした argv
    # に shim の禁止の旗が在れば 1 手番目が exit 2 で落ちる(2026-09-12 の本番の実弾の形)
    assert _shim_would_refuse(_texts(_obj(launched, "backend_ref"), "argv")) == []
    ended = _wait_turn_end(headless_host, "h-3")
    assert _text(ended, "status") == "running"
    assert ended["conversation"] == {"session_id": "thr-stub-1"}
    pid_before = _obj(ended, "backend_ref")["pid"]
    headless_host.ok("session.send", {"session_id": "h-3", "message": "again", "awaiting": True})
    ended_again = _wait_turn_end(headless_host, "h-3")
    assert _obj(ended_again, "backend_ref")["pid"] == pid_before  # 同じ process(温かい)
    headless_host.ok("session.cleanup", {"session_id": "h-3"})


def test_host_headless_startup_recovery_ends_the_dead_mid_turn_row_and_keeps_the_idle_one(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段 10 lane 10h(agora-redesign #84・実弾 2026-09-14 14:35): host の再起動(registry が消え、
    launchd の kickstart が子 process を道連れにする)の後、手番の途中のまま残った行は起動時の復帰が
    backend を観測して exited + vanished に倒す(session_exited・reason に pid)。2026-09-22 の改訂
    (R25・法 a-dead-backend-is-not-a-live-session)から、器の死んだ idle の温かい行も同じく倒す
    (手番の途中かどうかは reason の文だけを分ける)— その会話の次の手番は終端 ∧ 同じ家の resume の腕で
    --resume される。wire の backend_alive は観測から。"""
    # h-busy: 手番の途中(替え玉は result の前で 30 秒待つ)/ h-idle: 手番が終わった行(段 12 lane 12e・#517:
    # 手番の終わり = process の終わり — process は降りていて行だけが温かい)
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "30"
    busy = headless_host.ok("session.launch", _launch_params(headless_host.root, "h-busy", "claude"))
    assert isinstance(busy, dict)
    assert busy["awaiting_response"] is True
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "0"
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-idle", "claude"))
    idle = _wait_turn_end(headless_host, "h-idle")
    assert _has(idle, "turn_ended_at")
    # 生きている間の観測: 手番の途中の行だけ backend_alive(idle の行の process は手番の終わりで降りている)
    assert headless_host.snap("h-busy")["backend_alive"] is True
    _wait_until(lambda: headless_host.snap("h-idle")["backend_alive"] is False)
    busy_pid = _obj(headless_host.snap("h-busy"), "backend_ref")["pid"]
    assert isinstance(busy_pid, int)
    # 再起動の再現: launchd の kickstart -k は process group ごと殺す(子も死ぬ)→ 新しい host の registry は空
    old_registry = host.HEADLESS_REGISTRY
    os.kill(busy_pid, 9)
    _wait_until(lambda: not old_registry.has_alive("h-busy") and not old_registry.has_alive("h-idle"))
    monkeypatch.setattr(host, "HEADLESS_REGISTRY", HeadlessRegistry())
    # 復帰の前: 手番の途中の行は running のまま(誰も倒していない = 実弾の形)、backend_alive は観測で false
    before = headless_host.snap("h-busy")
    assert _text(before, "status") == "running"
    assert before["backend_alive"] is False
    # 2026-09-22 の改訂(R25・法 a-dead-backend-is-not-a-live-session): 器の死んだ行は手番の途中かどうかに依らず倒す —
    # 手番の途中かどうかは reason の文だけを分ける。
    outcomes = host.run_hosted(headless_host.config, headless_host.actor, headless_hy.recover_headless_rows())
    assert outcomes == {"h-busy": "exited", "h-idle": "exited"}
    after = headless_host.snap("h-busy")
    assert _text(after, "status") == "exited"
    assert after["awaiting_response"] is False
    cause = _obj(after, "terminal_cause")
    assert cause["category"] == "vanished"
    assert cause["retryable"] is True
    assert f"pid {busy_pid}" in _text(cause, "reason")
    assert "backend process dead" in _text(cause, "reason")
    assert "while the turn was in flight" in _text(cause, "reason")
    assert after["backend_alive"] is False
    folded = headless_host.snap("h-idle")
    assert _text(folded, "status") == "exited"
    assert _has(folded, "turn_ended_at")
    assert folded["backend_alive"] is False
    idle_cause = _obj(folded, "terminal_cause")
    assert idle_cause["category"] == "vanished"
    assert "and the row was idle between turns" in _text(idle_cause, "reason")
    # 復帰は冪等(2 度目は何も倒さない — 両方とも終端)
    again = host.run_hosted(headless_host.config, headless_host.actor, headless_hy.recover_headless_rows())
    assert again == {}
    # 倒した idle の行の会話は次の手番を --resume で受ける(終端 ∧ 同じ家 → resume の腕・cache は保つ)。
    # resume の admission が要る transcript(replaced CLI が書く projects/<mangled cwd>/<conv>.jsonl)を置く。
    conversation = _text(_obj(folded, "conversation"), "session_id")
    canonical = os.path.realpath(_text(folded, "work_dir"))
    mangled = "".join(ch if ch.isalnum() else "-" for ch in canonical)
    transcript = headless_host.root / "claude-home" / "projects" / mangled / f"{conversation}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    if not transcript.exists():
        transcript.write_text("{}\n", encoding="utf-8")
    resumed = headless_host.ok(
        "session.resume", {"session_id": "h-idle", "new_session_id": "h-idle-r", "prompt": "after restart"}
    )
    assert isinstance(resumed, dict)
    assert "--resume" in _texts(_obj(resumed, "backend_ref"), "argv")
    ended = _wait_turn_end(headless_host, "h-idle-r")
    assert _text(ended, "status") == "running"
    headless_host.ok("session.cleanup", {"session_id": "h-idle-r"})
    # 終端の行に対しても 3 度目の復帰は keep
    assert host.run_hosted(headless_host.config, headless_host.actor, headless_hy.recover_headless_rows()) == {}


def test_host_headless_resume_reads_a_row_whose_persisted_cause_lacks_the_contract_fields(
    headless_host: Host,
) -> None:
    """段 10 lane 10h(agora-redesign #84 副次・実弾 2026-09-14): headless の session.resume の腕の
    KeyError は 2 つ — (1) 手で書かれた terminal_cause({"cause": …}・category も observed_at も無い)の
    行は session.get も session.resume も読めず KeyError 'category'(store.hy terminal-cause-from-dict の
    get)で断られた(18:5x)。typed には cause なしとして読み、wire は raw を運ぶ。(2) launch.hy
    resume-session の launch-params が events_root を運ばず、headless-launch-session の
    (get params "events_root") で KeyError 'events_root'(本番 log 2 件)— headless の --resume は 1 度も
    通っておらず全部 rehydrate に落ちていた。この検は両方を実 host(replaced CLI)で通す。"""
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-6", "claude"))
    ended = _wait_turn_end(headless_host, "h-6")
    assert _text(ended, "status") == "running"
    headless_host.ok("session.cleanup", {"session_id": "h-6"})
    raw = json.dumps({"cause": "backend-process-dead", "by": "operator-delegate (stopgap)", "pid": 22663})
    def _write_raw_cause(conn: object) -> object:
        assert isinstance(conn, sqlite3.Connection)
        return conn.execute(
            "UPDATE agent_sessions SET terminal_cause_json = ? WHERE session_id = ?", (raw, "h-6")
        )

    headless_host.actor.submit(_write_raw_cause)
    snap = headless_host.snap("h-6")
    assert _text(snap, "status") == "stopped"
    assert snap["terminal_cause"] == json.loads(raw)  # wire は raw のまま
    # resume の admission が要る transcript(replaced CLI が書く projects/<mangled cwd>/<conv>.jsonl)を置く
    conversation = _text(_obj(snap, "conversation"), "session_id")
    canonical = os.path.realpath(_text(snap, "work_dir"))
    mangled = "".join(ch if ch.isalnum() else "-" for ch in canonical)
    transcript = headless_host.root / "claude-home" / "projects" / mangled / f"{conversation}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("{}\n", encoding="utf-8")
    resumed = headless_host.ok(
        "session.resume", {"session_id": "h-6", "new_session_id": "h-6-r", "prompt": "again"}
    )
    assert isinstance(resumed, dict)
    assert _text(resumed, "session_id") == "h-6-r"
    assert "--resume" in _texts(_obj(resumed, "backend_ref"), "argv")
    again = _wait_turn_end(headless_host, "h-6-r")
    assert _text(again, "status") == "running"
    headless_host.ok("session.cleanup", {"session_id": "h-6-r"})


def test_host_headless_stop_under_the_drain_marker_cuts_the_mid_turn_row_as_host_drained(headless_host: Host) -> None:
    """card acp:kanban-issue:ki-b5e0d04de958 D1: 停止の腕は印の答えを値で受け(読まない)、手番の途中の行を stopped +
    host_drained に倒す。散文は今日と同じ形(理由の欄に印の 1 行目が乗る — 判断には使わない)。"""
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "30"
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-busy", "claude"))
    busy_pid = _obj(headless_host.snap("h-busy"), "backend_ref")["pid"]
    assert isinstance(busy_pid, int)
    reason = f"SIGTERM; drain declared: {_POOL_MARKER.strip()}"
    outcomes = host.run_hosted(headless_host.config, headless_host.actor, headless_hy.stop_headless_rows(reason, True))
    assert outcomes == {"h-busy": "stopped", "killed": 1}
    _wait_until(lambda: not _pid_alive(busy_pid))
    cut = headless_host.snap("h-busy")
    assert _text(cut, "status") == "stopped"
    cause = _obj(cut, "terminal_cause")
    assert cause["category"] == "host_drained"
    assert _text(cause, "reason").startswith(f"sessionhost stopped ({reason}) while the turn was running")


def test_host_headless_stop_cuts_the_mid_turn_row_and_terminates_every_process(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段 10 lane 10h 便 2(agora-redesign #84): host の停止の腕は手番の途中の行を stopped + cancelled(理由 =
    host の停止)にして黙って残さず、idle の温かい行は触らず、登記の全 process を並列の猶予で降ろす。
    次の起動の復帰は stopped の行を keep(vanished に読み替えない)・器の降りた idle の行は倒す(R25 の改訂)。"""
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "30"
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-busy", "claude"))
    headless_host.stub_env["DOEFF_HEADLESS_STUB_DELAY"] = "0"
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-idle", "claude"))
    _wait_turn_end(headless_host, "h-idle")
    busy_pid = _obj(headless_host.snap("h-busy"), "backend_ref")["pid"]
    idle_pid = _obj(headless_host.snap("h-idle"), "backend_ref")["pid"]
    assert isinstance(busy_pid, int)
    assert isinstance(idle_pid, int)
    started = time.monotonic()
    outcomes = host.run_hosted(headless_host.config, headless_host.actor, headless_hy.stop_headless_rows("SIGTERM", False))
    assert outcomes == {"h-busy": "stopped", "h-idle": "running", "killed": 2}
    assert time.monotonic() - started < 12.0  # 並列の猶予(EOF 5 s + TERM 5 s を process の数だけ積まない)
    _wait_until(lambda: not _pid_alive(busy_pid) and not _pid_alive(idle_pid))
    cut = headless_host.snap("h-busy")
    assert _text(cut, "status") == "stopped"
    assert cut["awaiting_response"] is False
    cause = _obj(cut, "terminal_cause")
    assert cause["category"] == "cancelled"
    assert "sessionhost stopped (SIGTERM)" in _text(cause, "reason")
    kept = headless_host.snap("h-idle")
    assert _text(kept, "status") == "running"
    assert _has(kept, "turn_ended_at")
    assert host.HEADLESS_REGISTRY.names() == ()
    # 次の起動の復帰: stopped の行は終端 = keep(vanished に読み替えない)。停止の腕が触らなかった idle の行は、
    # 器が降りているので復帰が倒す(2026-09-22 の改訂 R25・法 a-dead-backend-is-not-a-live-session — 停止の腕と
    # 復帰の腕は別の判断で、idle の行を倒すのは観測を持つ復帰の側ちょうど)。
    monkeypatch.setattr(host, "HEADLESS_REGISTRY", HeadlessRegistry())
    recovered = host.run_hosted(headless_host.config, headless_host.actor, headless_hy.recover_headless_rows())
    assert recovered == {"h-idle": "exited"}
    assert _text(headless_host.snap("h-busy"), "status") == "stopped"
    idle_after = headless_host.snap("h-idle")
    assert _text(idle_after, "status") == "exited"
    assert _obj(idle_after, "terminal_cause")["category"] == "vanished"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _spawn_real_headless_host(root: Path, *, drain_file: Path | None = None) -> subprocess.Popen[str]:
    """実 binary の headless の host を tmpdir で起こす(替え玉の claude・result の前で 30 秒待つ)。

    宿から隔離する(sessionhost_isolated_host の頭注): HOME / 資格の置き場は検の私設・画面判定は無効。
    PATH の先頭は替え玉(tests/headless_stubs)で、その後ろに本物の CLI の罠が続く。
    drain_file = 排水の印の path を env で渡す(役 host の起動の形・card ki-b5e0d04de958 D1)— None = 渡さない。
    """
    isolated = isolated_host(root / "host")
    env = dict(isolated.env)
    env["PATH"] = f"{STUBS}{os.pathsep}{env['PATH']}"
    env["DOEFF_SESSIONHOST_HEADLESS_DIR"] = str(root / "events")
    env["DOEFF_HEADLESS_STUB_DELAY"] = "30"
    env["XDG_STATE_HOME"] = str(root / "state")
    env.pop("DOEFF_AGENTD_ACP", None)
    env.pop(host.ENV_DRAIN_FILE, None)
    if drain_file is not None:
        env[host.ENV_DRAIN_FILE] = str(drain_file)
    with (root / "host.log").open("w", encoding="utf-8") as log:
        return subprocess.Popen(
            sessionhost_serve_argv(
                resolve_sessionhost_bin(),
                db_path=root / "agentd.sqlite",
                socket_path=root / "agentd.sock",
                extra_args=("--backend", "headless"),
            ),
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
        )


def _wait_real_host(proc: subprocess.Popen[str], root: Path) -> None:
    from doeff_agents.agentd_client import AgentdClient

    client = AgentdClient(root / "agentd.sock", timeout=2.0)
    deadline = time.monotonic() + 15.0
    while True:
        if proc.poll() is not None:
            raise AssertionError(f"host exited early: {proc.returncode}\n{(root / 'host.log').read_text(encoding='utf-8')}")
        try:
            client.status()
            return
        except Exception:
            if time.monotonic() > deadline:
                raise AssertionError(f"host did not come up\n{(root / 'host.log').read_text(encoding='utf-8')}") from None
            _pause(0.1)


class _StoredRow(NamedTuple):
    status: str
    awaiting: int
    cause: JSONObject


def _stored_row(root: Path, session_id: str) -> _StoredRow:
    conn = sqlite3.connect(root / "agentd.sqlite")
    try:
        row = conn.execute(
            "SELECT status, awaiting_response, terminal_cause_json FROM agent_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    cause = json.loads(row[2])
    assert isinstance(cause, dict)
    return _StoredRow(str(row[0]), int(row[1]), cause)


def test_real_host_sigterm_closes_the_running_turn_before_exit() -> None:
    """段 10 lane 10h 便 2(agora-redesign #84・実 binary): headless の host に TERM を送ると、exit の前に
    手番の途中の行が stopped + cancelled になり、子 process が降り、lease が釈放される(黙って道連れにしない)。
    2 度目の TERM(撃ち直し)で SystemExit → finally の lease 釈放まで届く。"""
    from doeff_agents.agentd_client import AgentdClient

    root = Path(tempfile.mkdtemp(prefix="doeff-headless-term-"))
    try:
        proc = _spawn_real_headless_host(root)
        try:
            _wait_real_host(proc, root)
            client = AgentdClient(root / "agentd.sock", timeout=2.0)
            launched = client.request("session.launch", _launch_params(root, "h-term", "claude"))
            assert isinstance(launched, dict)
            assert launched["awaiting_response"] is True
            child_pid = _obj(launched, "backend_ref")["pid"]
            assert isinstance(child_pid, int)
            assert _pid_alive(child_pid)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30.0)
            assert proc.returncode == 0, (root / "host.log").read_text(encoding="utf-8")
            _wait_until(lambda: not _pid_alive(child_pid))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)
        text = (root / "host.log").read_text(encoding="utf-8")
        assert "doeff-sessionhost stop (SIGTERM): closing running turns before exit" in text
        assert "1 headless process(es) terminated, 1 mid-turn row(s) ended as stopped/cancelled: h-term" in text
        assert "doeff-sessionhost lease released on shutdown" in text
        status, awaiting, cause = _stored_row(root, "h-term")
        assert status == "stopped"
        assert awaiting == 0
        assert cause["category"] == "cancelled"
        assert "sessionhost stopped (SIGTERM)" in _text(cause, "reason")
    finally:
        shutil.rmtree(root, ignore_errors=True)


_POOL_MARKER = "pool-prestop agentd-pool-1 0f3c-uid 2026-09-24T00:00:00Z pod termination\n"


class _TermedTurn(NamedTuple):
    """TERM で切った手番の行と host の log(_term_one_running_turn の答え)。"""

    stored: _StoredRow
    host_log: str


def _term_one_running_turn(root: Path, drain_file: Path | None) -> _TermedTurn:
    """実 binary の host を起こし、手番 1 本の途中で TERM を 1 度送って降りるのを待つ(切った行と host の log)。"""
    from doeff_agents.agentd_client import AgentdClient

    proc = _spawn_real_headless_host(root, drain_file=drain_file)
    try:
        _wait_real_host(proc, root)
        client = AgentdClient(root / "agentd.sock", timeout=2.0)
        launched = client.request("session.launch", _launch_params(root, "h-drain", "claude"))
        assert isinstance(launched, dict)
        assert launched["awaiting_response"] is True
        child_pid = _obj(launched, "backend_ref")["pid"]
        assert isinstance(child_pid, int)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30.0)
        assert proc.returncode == 0, (root / "host.log").read_text(encoding="utf-8")
        _wait_until(lambda: not _pid_alive(child_pid))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)
    return _TermedTurn(_stored_row(root, "h-drain"), (root / "host.log").read_text(encoding="utf-8"))


def test_real_host_sigterm_under_the_drain_marker_cuts_the_turn_as_host_drained_and_leaves_the_marker_alone() -> None:
    """card acp:kanban-issue:ki-b5e0d04de958 D1(受入 2 の (a)・実 binary): 停止の拍に排水の印が在れば、切った行の cause は
    host_drained(計画された入れ替え)・散文に印の 1 行目が足される・log の数える語も行の語。host は印を読むだけで、
    **file の中身は停止の前後で 1 byte も変わらない**(上書きしない・消さない — 書き手は doeff の外)。"""
    root = Path(tempfile.mkdtemp(prefix="doeff-headless-drain-"))
    try:
        marker = root / "drain"
        marker.write_text(_POOL_MARKER, encoding="utf-8")
        before = marker.read_bytes()
        stored, text = _term_one_running_turn(root, marker)
        assert stored.status == "stopped"
        assert stored.awaiting == 0
        assert stored.cause["category"] == "host_drained"
        assert "sessionhost stopped (SIGTERM; drain declared: pool-prestop agentd-pool-1 0f3c-uid" in _text(stored.cause, "reason")
        assert "1 mid-turn row(s) ended as stopped/host_drained: h-drain" in text
        assert marker.read_bytes() == before
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_real_host_sigterm_without_the_marker_stays_cancelled_and_the_host_writes_no_marker() -> None:
    """同(受入 2 の (b)・盲検 B の反例の 1 層目): env が印の path を名指していても、停止の拍に印が無ければ行の cause は
    今日どおり cancelled。**停止の前に無かった印の file は停止の後も無い** — host が自分で印を作ると、印の無い停止が
    計画された停止の予算へ移る(card の受入 2 に反する)。"""
    root = Path(tempfile.mkdtemp(prefix="doeff-headless-nodrain-"))
    try:
        marker = root / "drain"
        assert not marker.exists()
        stored, text = _term_one_running_turn(root, marker)
        assert not marker.exists(), f"the host created the drain marker itself:\n{text}"
        assert stored.status == "stopped"
        assert stored.cause["category"] == "cancelled"
        assert "drain declared" not in _text(stored.cause, "reason")
        assert "1 mid-turn row(s) ended as stopped/cancelled: h-drain" in text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_real_host_sigterm_without_the_env_stays_cancelled_even_with_a_marker_at_the_default_place() -> None:
    """同(受入 2 の (c)): env を渡さない起動(役 both・join を通らない serve)は印を読まない — 既定の置き場
    (runtime.drain_file_path が XDG_STATE_HOME から導く `<state>/doeff/acp-agentd/drain`)に印が在っても、host は
    path を自分で導かないので cancelled のまま(置き場の定義点は ACP 側の 1 つ・host は渡された path だけを読む)。"""
    root = Path(tempfile.mkdtemp(prefix="doeff-headless-noenv-"))
    try:
        default_place = root / "state" / "doeff" / "acp-agentd" / "drain"
        default_place.parent.mkdir(parents=True)
        default_place.write_text(_POOL_MARKER, encoding="utf-8")
        stored, text = _term_one_running_turn(root, None)
        assert stored.cause["category"] == "cancelled"
        assert "1 mid-turn row(s) ended as stopped/cancelled: h-drain" in text
        assert default_place.read_text(encoding="utf-8") == _POOL_MARKER
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_host_headless_run_to_completion_ends_done_and_is_swept(headless_host: Host) -> None:
    headless_host.ok(
        "session.launch",
        _launch_params(headless_host.root, "h-4", "claude", lifecycle="run_to_completion"),
    )
    ended = _wait_turn_end(headless_host, "h-4")
    assert _text(ended, "status") == "done"
    headless_host.monitor()
    assert _has(headless_host.snap("h-4"), "cleaned_at")


def test_host_headless_rejects_tmux_backend_vocabulary_for_unknown_backend() -> None:
    with pytest.raises(ValueError, match=r"tmux\|herdr\|headless"):
        host.parse_args(["--backend", "pane", "serve"])


# ---------------------------------------------------------------- 段 10 lane 10o: 添付の綴りの凍結


PNG_B64 = "iVBORw0KGgo="
JPEG_B64 = "/9j/4AAQSkZJRg=="


def _png(name: str = "red.png") -> TurnAttachment:
    return TurnAttachment(mime="image/png", data=PNG_B64, bytes=8, sha256="a" * 64, name=name)


def test_claude_turn_and_injection_carry_the_measured_image_block() -> None:
    """段 10 lane 10o(agora-redesign #96・conformance/attachment-physics.md の実測): claude の
    添付の綴りは Messages API と同じ image の block で、手番の行にも注入の行にも同じ形で載る。
    綴りの座は Dialogue 1 点 — agentd は型つきの TurnAttachment を渡すだけ(法 012 R21)。"""
    dialogue = ClaudeDialogue()
    turn = dialogue.turn(_content("見て", (_png(), TurnAttachment(mime="image/jpeg", data=JPEG_B64))))
    record = _record(turn.sends[0])
    message = record["message"]
    assert isinstance(message, dict)
    assert message["content"] == [
        {"type": "text", "text": "見て"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": JPEG_B64}},
    ]
    # 添付の無い手番の綴りは 1 byte も変わらない(content は素の文字列のまま)。
    plain = _record(dialogue.turn(_content("やあ")).sends[0])
    plain_message = plain["message"]
    assert isinstance(plain_message, dict)
    assert plain_message["content"] == "やあ"
    # 本文が空で添付だけの郵便も通る(image の block だけ)。
    only = _record(dialogue.turn(_content("", (_png(),))).sends[0])
    only_message = only["message"]
    assert isinstance(only_message, dict)
    assert only_message["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}}
    ]
    # 注入(走っている手番への割り込み)も同じ形 + 行の名(uuid)。
    dialogue.on_line(_init())
    injected = dialogue.inject(_content("これも見て", (_png(),)), "msg-1")
    assert injected.accepted is True
    injected_record = _record(injected.sends[0])
    assert injected_record["uuid"] == "msg-1"
    injected_message = injected_record["message"]
    assert isinstance(injected_message, dict)
    assert injected_message["content"] == [
        {"type": "text", "text": "これも見て"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}},
    ]


def _opened_codex() -> CodexDialogue:
    """thread が開いた codex の Dialogue(検の下ごしらえ — 綴りは既存の握手の検と同じ)。"""
    dialogue = CodexDialogue(CodexPlan(cwd="/w"))
    opening = dialogue.opening()
    init_reply = dialogue.on_line({"id": _rpc(opening[0])["id"], "result": {}})
    thread_start = _rpc(init_reply.sends[1])
    dialogue.on_line({"id": thread_start["id"], "result": {"thread": {"id": "thr-1"}}})
    return dialogue


def test_codex_turn_carries_the_measured_data_url_item() -> None:
    """段 10 lane 10o: codex の添付は turn/start の input の data URL の image の項(実測: data URL と
    localImage は API へ同じ input_image になるので、agentd は一時 file を作らない)。"""
    dialogue = _opened_codex()
    started = dialogue.turn(_content("見て", (_png(),)))
    params = _obj(_rpc(started.sends[0]), "params")
    assert params["input"] == [
        {"type": "text", "text": "見て"},
        {"type": "image", "url": f"data:image/png;base64,{PNG_B64}"},
    ]
    # 添付の無い手番の綴りは変わらない(text の項だけ)。
    plain = _obj(_rpc(dialogue.turn(_content("やあ")).sends[0]), "params")
    assert plain["input"] == [{"type": "text", "text": "やあ"}]
    # 本文が空で添付だけの郵便は image の項だけ(空の text の項を作らない)。
    only = _obj(_rpc(dialogue.turn(_content("", (_png(),))).sends[0]), "params")
    assert only["input"] == [{"type": "image", "url": f"data:image/png;base64,{PNG_B64}"}]


def test_codex_injection_concatenation_keeps_both_texts_and_both_images() -> None:
    """段 10 lane 10o: turn/start がまだ積まれていない間に来た 2 通目の割り込みは、本文を空行で繋ぎ
    添付を順に並べる(本文が文字列の連結だけだった頃は 2 通目の画像が落ちる形だった)。"""
    dialogue = _opened_codex()
    started = dialogue.turn(_content("work"))
    turn_start = _rpc(started.sends[0])
    dialogue.on_line({"id": turn_start["id"], "result": {"turn": {"id": "turn-1"}}})
    first = dialogue.inject(_content("止めて", (_png("one.png"),)), "msg-1")
    assert first.accepted is True
    second = dialogue.inject(
        _content("これも", (TurnAttachment(mime="image/jpeg", data=JPEG_B64),)), "msg-2"
    )
    assert second.accepted is True and second.sends == ()
    step = dialogue.on_line(
        {
            "method": "turn/completed",
            "params": {"threadId": "thr-1", "turn": {"id": "turn-1", "status": "interrupted"}},
        }
    )
    params = _obj(_rpc(step.sends[0]), "params")
    assert params["input"] == [
        {"type": "text", "text": "止めて\n\nこれも"},
        {"type": "image", "url": f"data:image/png;base64,{PNG_B64}"},
        {"type": "image", "url": f"data:image/jpeg;base64,{JPEG_B64}"},
    ]


# ---------------------------------------------------------------- 実 binary の smoke(API を撃たない)


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude binary is not installed here")
def test_real_claude_accepts_the_headless_flags_help_only(tmp_path: Path) -> None:
    built = _build_claude_headless(
        {"work_dir": os.getcwd(), "session_hooks": "disabled"}
    )
    argv = built["argv"]
    assert isinstance(argv, list)
    binary = argv[0]
    assert isinstance(binary, str)
    resolved = shutil.which(binary)
    assert resolved is not None, binary
    # 本物の binary は --help だけ撃つ(API を撃たない)が、HOME / CLAUDE_CONFIG_DIR は宿のものを
    # 渡さない(sessionhost_isolated_host の頭注 — 検は宿の資格の置き場に触れない)。binary は
    # 宿の PATH で先に解いてから、隔離した env(PATH の先頭は罠)で起こす。
    host = isolated_host(tmp_path / "host")
    result = subprocess.run(
        [resolved, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=dict(host.env),
    )
    assert result.returncode == 0
    for flag in ("--output-format", "--include-partial-messages", "--session-id", "--resume"):
        assert flag in result.stdout


def test_host_headless_monitor_stamps_the_turn_end_from_the_current_row_not_the_cycles_listing(
    headless_host: Host,
) -> None:
    """段 11 lane 11y 便 3(agora-redesign #140・依頼者の裁定 2026-09-16): monitor の拍は最初に全行を
    列挙してから 1 行ずつ器を観測する。列挙の写しの後に届いた session.send(awaiting True・
    turn_ended_at None)は、写しで導出して書き戻すと失われ、旧形の `(or row.turn-ended-at observed-at)`
    が前の手番の印を保つ —— agentd の job-step-of は turn_ended_at ≤ floor(送った時刻)で observe を
    続け、手番が終わった job が Running のまま残る(実弾 2026-09-16 07:00 JST・aj-W9WT…: 6 分・割り込み
    4 通が走っていない手番に置かれたまま)。反例 = 古い写しで観測した後、手番の終わりの印が前の
    手番の値のまま(送った時刻より前)なら赤。直し = 導出は行の今の値から・印はこの観測の時刻。
    """
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-stale", "claude"))
    first = _wait_turn_end(headless_host, "h-stale")
    assert _text(first, "status") == "running"
    first_stamp = _text(first, "turn_ended_at")
    # monitor の拍が拍の頭で列挙した写し(手番 1 の終わりの印・awaiting False)
    listed = host.run_hosted(
        headless_host.config, headless_host.actor, headless_hy.session_store_list_active()
    )
    assert isinstance(listed, list)
    stale = next(row for row in listed if row.session_id == "h-stale")
    assert stale.awaiting_response is False
    assert stale.turn_ended_at == first_stamp
    # 写しの後に次の手番が届く(awaiting True・turn_ended_at None・温かい同じ process へ)
    headless_host.ok(
        "session.send", {"session_id": "h-stale", "message": "second turn", "awaiting": True}
    )
    sent = headless_host.snap("h-stale")
    assert sent["awaiting_response"] is True
    assert not _has(sent, "turn_ended_at")
    sent_at = _text(sent, "last_observed_at")  # send が刻む「送った拍」(host の同じ時計・同じ綴り)
    # 替え玉が 2 手番目の result を出すまで待つ(器の観測は撃たない — ended は器に溜まったまま)
    events_path = Path(_text(_obj(sent, "backend_ref"), "events_path"))

    def _two_results() -> bool:
        lines = events_path.read_text(encoding="utf-8").splitlines()
        return sum(1 for line in lines if _record(line)["type"] == "result") >= 2

    _wait_until(_two_results)
    # 実弾の拍の形: 古い写しで観測する
    host.run_hosted(headless_host.config, headless_host.actor, headless_hy.observe_headless_row(stale))
    after = headless_host.snap("h-stale")
    assert _text(after, "status") == "running"
    assert after["awaiting_response"] is False
    assert _has(after, "turn_ended_at")
    stamped = _text(after, "turn_ended_at")
    # 手番 2 の終わりの印は手番 2 の始まり(送った時刻)より後 — 前の手番の印(floor より前)のままなら赤
    assert stamped > sent_at, (stamped, sent_at, first_stamp)
    assert stamped > first_stamp
    headless_host.ok("session.cleanup", {"session_id": "h-stale"})


# ---------------------------------------------------------------- 4. 手番の終わりの合図(段 12 lane 12b・agora-redesign #207 根 1)


def test_headless_registry_wakes_the_waiter_the_moment_a_turn_ends(tmp_path: Path) -> None:
    """登記簿の待ち手(host の monitor)は、読み手が Dialogue で手番の終わりを読んだ拍に起きる — monitor の周期
    (1 s)を待たない。合図の数は待っている間も進み(取りこぼさない)、終わりが無ければ上限で返る。"""
    registry = HeadlessRegistry()
    events = str(tmp_path / "s-wake.events.jsonl")
    process = registry.spawn(
        "s-wake",
        ["claude", "-p", "--input-format", "stream-json", "--session-id", "sid-wake"],
        str(tmp_path),
        _stub_env(),
        events,
        ClaudeDialogue(),
    )
    # 終わりが無い間は上限で返り、数は 0 のまま。
    started = time.monotonic()
    assert registry.wait_turn_end(0, 0.1) == 0
    assert time.monotonic() - started < 1.0
    assert process.deliver("hello there") is True
    started = time.monotonic()
    count = registry.wait_turn_end(0, 5.0)
    waited = time.monotonic() - started
    assert count == 1
    assert waited < 1.0, waited
    # 合図の後に観測すると、束に終わりが在る(合図は束より後に出る — 待ち手が空の束を見ることは無い)。
    observed = process.observe()
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    # 見た数を名乗って待つと、次の終わりまで返らない(上限)。
    assert registry.wait_turn_end(count, 0.1) == 1
    # 次の手番の終わりで 2 へ(段 12 lane 12e・#517: 手番の終わりで process は降りるので、次の手番は同じ名の
    # --resume の process — 合図の数は登記簿のもので process をまたいで進む)。
    _wait_until(lambda: not process.alive())
    resumed = registry.spawn(
        "s-wake",
        ["claude", "-p", "--input-format", "stream-json", "--resume", "sid-wake"],
        str(tmp_path),
        _stub_env(),
        events,
        ClaudeDialogue(),
    )
    assert resumed.deliver("again") is True
    assert registry.wait_turn_end(1, 5.0) == 2
    registry.kill("s-wake")


def test_host_wait_events_returns_when_the_journal_advances_and_at_the_bound_otherwise(headless_host: Host) -> None:
    """RPC session.wait_events(出来事の journal の long-poll): after より先端が進めば即座に返り、進まなければ
    wait_seconds の上限で今の先端を返す。答えは先端の seq だけ(中身は運ばない — 呼び手は眺めを読み直す)。
    引数の形が違えば断る。"""
    seq0 = headless_host.ok("session.wait_events", {"after": 0, "wait_seconds": 0})
    assert isinstance(seq0, dict) and isinstance(seq0["seq"], int)
    head = seq0["seq"]
    # 進まない間は上限で返る(先端は同じ)。
    started = time.monotonic()
    bound = headless_host.ok("session.wait_events", {"after": head, "wait_seconds": 0.2})
    assert isinstance(bound, dict) and bound["seq"] == head
    assert 0.15 <= time.monotonic() - started < 1.5
    # 待っている間に出来事が記帳されると、上限を待たずに返る。
    answer: dict[str, JSON] = {}

    def waiter() -> None:
        answer["seq"] = headless_host.ok("session.wait_events", {"after": head, "wait_seconds": 5})
        answer["at"] = time.monotonic()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    _pause(0.1)
    fired = time.monotonic()
    sid = "s-journal"
    launched = headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    assert isinstance(launched, dict)
    thread.join(5.0)
    assert not thread.is_alive(), "wait_events did not return after the launch was journaled"
    got = answer["seq"]
    assert isinstance(got, dict) and isinstance(got["seq"], int) and got["seq"] > head
    at = answer["at"]
    assert isinstance(at, float) and at - fired < 1.0
    # 形の違う引数は断る(黙って 0 に倒さない)。
    for bad in ({"after": -1}, {"after": "0"}, {"after": True}, {"wait_seconds": -1}, {"wait_seconds": "1"}):
        response = headless_host.call("session.wait_events", dict(bad))
        assert response["ok"] is False, response
        assert "invalid params for session.wait_events" in str(response["error"])
    _wait_turn_end(headless_host, sid)
    headless_host.ok("session.cleanup", {"session_id": sid})


def test_fast_jev_compaction_enabled_reads_only_a_settings_that_makes_the_plugin_effective() -> None:
    """plugin が真で function hooks の env が 1 の時だけ真。片方でも欠けると `/compact` は組込みの
    要約(model 1 回)に落ちるので、その profile では圧縮の prompt を撃たない。"""
    on = json.dumps({"enabledPlugins": {"fast-jev-compaction@fast-jev-compaction": True},
                     "env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}})
    assert run(fast_jev.fast_jev_compaction_enabled(on)) is True
    assert run(headless_argv.fast_jev_compaction_enabled(on)) is True
    no_env = json.dumps({"enabledPlugins": {"fast-jev-compaction@fast-jev-compaction": True}})
    assert run(fast_jev.fast_jev_compaction_enabled(no_env)) is False
    off = json.dumps({"enabledPlugins": {"fast-jev-compaction@fast-jev-compaction": False},
                      "env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}})
    assert run(fast_jev.fast_jev_compaction_enabled(off)) is False
    assert run(fast_jev.fast_jev_compaction_enabled(None)) is False
    assert run(fast_jev.fast_jev_compaction_enabled("not json")) is False
    assert run(fast_jev.fast_jev_compaction_enabled("[]")) is False


def _enable_fast_jev_plugin(root: Path) -> None:
    home = root / "claude-home"
    home.mkdir(exist_ok=True)
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"fast-jev-compaction@fast-jev-compaction": True},
        "env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"},
    }))


def test_host_headless_send_runs_the_cold_compaction_prompt_before_the_resumed_turn_only_when_the_plugin_is_on(
    headless_host: Host,
) -> None:
    """冷えた再開の前の圧縮(2026-09-22): 続きの手番(--resume の新しい process)の**前**に、同じ
    実効 env で `claude -p "/compact fast-jev-if-cold" --resume <sid>` が 1 回走る — ただし profile の
    settings.json で圧縮 plugin が実際に効く形の時だけ。plugin の無い profile(会社)では 1 度も
    走らない(走ると組込みの要約に落ちる)。初手番の前には走らない(圧縮する歴史が無い)。"""
    log = headless_host.root / "argv.log"
    headless_host.stub_env["DOEFF_HEADLESS_STUB_ARGV_LOG"] = str(log)
    # plugin なし: 続きの手番の前に圧縮の prompt は走らない
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-cold-off", "claude"))
    _wait_turn_end(headless_host, "h-cold-off")
    headless_host.ok("session.send", {"session_id": "h-cold-off", "message": "second", "awaiting": True})
    _wait_turn_end(headless_host, "h-cold-off")
    off_lines = log.read_text().splitlines()
    assert not [line for line in off_lines if "/compact fast-jev-if-cold" in line], off_lines
    # plugin あり: 続きの手番の前に 1 回だけ、同じ --resume で走り、その後に手番の process が起きる
    _enable_fast_jev_plugin(headless_host.root)
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-cold-on", "claude"))
    _wait_turn_end(headless_host, "h-cold-on")
    before = len(log.read_text().splitlines())
    headless_host.ok("session.send", {"session_id": "h-cold-on", "message": "second", "awaiting": True})
    ended = _wait_turn_end(headless_host, "h-cold-on")
    assert _text(ended, "status") == "running", ended
    lines = log.read_text().splitlines()[before:]
    turns = [i for i, line in enumerate(lines) if "--input-format stream-json" in line and "--resume " in line]
    assert len(turns) == 1, lines
    resumed_id = lines[turns[0]].split("--resume ", 1)[1].split(" ", 1)[0]  # 手番が続ける会話の id
    assert resumed_id, lines
    compactions = [i for i, line in enumerate(lines) if f"-p /compact fast-jev-if-cold --resume {resumed_id}" in line]
    assert len(compactions) == 1, lines
    assert compactions[0] < turns[0], lines
    assert "stream-json" not in lines[compactions[0]]
    # 初手番(--session-id)の前には走っていない
    first = [line for line in log.read_text().splitlines() if "--session-id " in line]
    assert first and all("/compact" not in line for line in first), first
    for sid in ("h-cold-off", "h-cold-on"):
        headless_host.ok("session.cleanup", {"session_id": sid})


def test_fast_jev_cache_surely_warm_mirrors_the_plugin_cold_reason_and_only_says_warm_when_certain() -> None:
    """確かに温かい続きの読み(card acp:kanban-issue:ki-e786e72e2ae7): plugin の coldReason と同じ規則
    (状態が在り・configDir と model が同じ・経過が TTL 以内)で、温かいと**確かに**言える時だけ True。
    状態なし・壊れた状態・別の口座・別の model・TTL 切れ・TTL の終わりの余白・model 不明はすべて False。"""
    settings = json.dumps({"pluginConfigs": {"fast-jev-compaction@fast-jev-compaction": {
        "options": {"cacheTtlMinutes": 60, "stateDir": "/state/"}}}})
    now = 1_790_000_000_000
    state = json.dumps({"at": now - 10_000, "configDir": "/home/a", "model": "m"})
    warm = fast_jev.fast_jev_cache_surely_warm
    assert run(warm(settings, state, "/home/a", "m", now)) is True
    assert run(warm(settings, None, "/home/a", "m", now)) is False
    assert run(warm(settings, "not json", "/home/a", "m", now)) is False
    assert run(warm(settings, json.dumps({"at": "x", "configDir": "/home/a", "model": "m"}), "/home/a", "m", now)) is False
    assert run(warm(settings, state, "/home/b", "m", now)) is False
    assert run(warm(settings, state, "/home/a", "other", now)) is False
    assert run(warm(settings, state, "/home/a", None, now)) is False
    assert run(warm(settings, state, "/home/a", "m", now + 2 * 3_600_000)) is False
    # TTL の終わりの 60 秒の余白は温かいと読まない
    edge = json.dumps({"at": now - (60 * 60_000 - 30_000), "configDir": "/home/a", "model": "m"})
    assert run(warm(settings, edge, "/home/a", "m", now)) is False
    # options に TTL が無い家は plugin の既定(5 分)
    bare = json.dumps({"pluginConfigs": {"fast-jev-compaction@fast-jev-compaction": {"options": {"stateDir": "/s"}}}})
    six_minutes = json.dumps({"at": now - 6 * 60_000, "configDir": "/home/a", "model": "m"})
    assert run(warm(bare, six_minutes, "/home/a", "m", now)) is False
    assert run(fast_jev.fast_jev_session_state_path(settings, "sid/1")) == "/state/sid_1.json"
    assert run(fast_jev.fast_jev_session_state_path(bare, "sid")) == "/s/sid.json"
    assert run(fast_jev.fast_jev_session_state_path(None, "sid")) is None


def test_host_headless_send_skips_the_cold_compaction_process_only_when_the_plugin_state_says_warm(
    headless_host: Host,
) -> None:
    """確かに温かい続き(card acp:kanban-issue:ki-e786e72e2ae7): plugin の状態 file が同じ口座・同じ model・TTL 以内を
    名乗る続きの手番では、`claude -p "/compact fast-jev-if-cold"` の process を起こさない(起こしても plugin は
    'cache warm; untouched' で何もしない — 費用は claude の起動 1 回ぶんの待ちだけ)。状態が古い(TTL 切れ)・
    別の口座の続きは今日どおり手番の前に 1 回走る(冷えた再開の前の圧縮は保つ)。"""
    log = headless_host.root / "argv.log"
    headless_host.stub_env["DOEFF_HEADLESS_STUB_ARGV_LOG"] = str(log)
    state_dir = headless_host.root / "fast-jev-state"
    state_dir.mkdir()
    home = headless_host.root / "claude-home"
    home.mkdir(exist_ok=True)
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"fast-jev-compaction@fast-jev-compaction": True},
        "env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"},
        "pluginConfigs": {"fast-jev-compaction@fast-jev-compaction": {
            "options": {"cacheTtlMinutes": 60, "stateDir": str(state_dir)}}},
    }))
    sid = "h-cold-warm"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    _wait_turn_end(headless_host, sid)
    conversation = _text(_obj(headless_host.snap(sid), "conversation"), "session_id")

    def _send_and_count(state: JSONObject | None) -> int:
        path = state_dir / f"{conversation}.json"
        if state is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(state))
        before = len(log.read_text().splitlines())
        headless_host.ok("session.send", {"session_id": sid, "message": "again", "awaiting": True})
        ended = _wait_turn_end(headless_host, sid)
        assert _text(ended, "status") == "running", ended
        lines = log.read_text().splitlines()[before:]
        assert [line for line in lines if "--input-format stream-json" in line and "--resume " in line], lines
        return len([line for line in lines if f"-p /compact fast-jev-if-cold --resume {conversation}" in line])

    now_ms = int(time.time() * 1000)
    same = {"configDir": str(home), "model": "stub-model"}
    assert _send_and_count({"at": now_ms - 5_000, **same}) == 0
    assert _send_and_count({"at": now_ms - 2 * 3_600_000, **same}) == 1
    assert _send_and_count({"at": now_ms - 5_000, "configDir": "/elsewhere", "model": "stub-model"}) == 1
    assert _send_and_count(None) == 1
    headless_host.ok("session.cleanup", {"session_id": sid})


def test_host_headless_resume_launch_runs_the_cold_compaction_prompt_before_the_first_resumed_turn(
    headless_host: Host,
) -> None:
    """冷えた再開の前の圧縮(2026-09-22 追補・実測 pool -1 06:36): 会話を**この家で初めて**続ける起動
    (session.resume — pod の入れ替え後の続きはこの腕)は冷えた再開そのもの。続きの手番の腕と同じく、
    plugin が効く profile では起動の process の**前**に `claude -p "/compact fast-jev-if-cold" --resume <sid>`
    が 1 回走り、plugin の無い profile では走らない。"""
    log = headless_host.root / "argv.log"
    headless_host.stub_env["DOEFF_HEADLESS_STUB_ARGV_LOG"] = str(log)

    def _resume(sid: str) -> str:
        headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
        _wait_turn_end(headless_host, sid)
        headless_host.ok("session.cleanup", {"session_id": sid})
        snap = headless_host.snap(sid)
        conversation = _text(_obj(snap, "conversation"), "session_id")
        canonical = os.path.realpath(_text(snap, "work_dir"))
        mangled = "".join(ch if ch.isalnum() else "-" for ch in canonical)
        transcript = headless_host.root / "claude-home" / "projects" / mangled / f"{conversation}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text("{}\n", encoding="utf-8")
        before = len(log.read_text().splitlines())
        headless_host.ok("session.resume", {"session_id": sid, "new_session_id": f"{sid}-r", "prompt": "again"})
        again = _wait_turn_end(headless_host, f"{sid}-r")
        assert _text(again, "status") == "running", again
        headless_host.ok("session.cleanup", {"session_id": f"{sid}-r"})
        return "\n".join(log.read_text().splitlines()[before:])

    # plugin なし: 起動の前に圧縮の prompt は走らない
    off = _resume("h-cold-launch-off")
    assert "--resume " in off, off
    assert "/compact fast-jev-if-cold" not in off, off
    # plugin あり: 起動の process の前に 1 回だけ、同じ --resume で走る
    _enable_fast_jev_plugin(headless_host.root)
    lines = _resume("h-cold-launch-on").splitlines()
    turns = [i for i, line in enumerate(lines) if "--input-format stream-json" in line and "--resume " in line]
    assert len(turns) == 1, lines
    resumed_id = lines[turns[0]].split("--resume ", 1)[1].split(" ", 1)[0]
    compactions = [i for i, line in enumerate(lines) if f"-p /compact fast-jev-if-cold --resume {resumed_id}" in line]
    assert len(compactions) == 1, lines
    assert compactions[0] < turns[0], lines
    assert "stream-json" not in lines[compactions[0]]


def test_fast_jev_plugin_version_pin_reads_installed_version_and_decides_update() -> None:
    """借りた家の plugin は宣言の版(FAST_JEV_PLUGIN_VERSION)へ揃える: plugin.json の版が pin と違う時だけ
    update を撃つ。読めない(file 無し・壊れた JSON・version 無し)は「古い」ではなく「読めない」で、撃たない。"""
    pin = fast_jev.FAST_JEV_PLUGIN_VERSION
    assert run(fast_jev.fast_jev_plugin_json_path("/h/claude-home")) == "/h/claude-home/plugins/marketplaces/fast-jev-compaction/.claude-plugin/plugin.json"
    assert run(fast_jev.fast_jev_plugin_json_path("/h/claude-home/")) == "/h/claude-home/plugins/marketplaces/fast-jev-compaction/.claude-plugin/plugin.json"
    assert run(fast_jev.fast_jev_installed_version(json.dumps({"name": "fast-jev-compaction", "version": "0.4.6"}))) == "0.4.6"
    assert run(fast_jev.fast_jev_installed_version(json.dumps({"version": " 0.5.0 "}))) == "0.5.0"
    assert run(fast_jev.fast_jev_installed_version(None)) is None
    assert run(fast_jev.fast_jev_installed_version("not json")) is None
    assert run(fast_jev.fast_jev_installed_version(json.dumps({"name": "x"}))) is None
    assert run(fast_jev.fast_jev_installed_version(json.dumps({"version": ""}))) is None
    assert run(fast_jev.fast_jev_plugin_outdated("0.4.6")) is True
    assert run(fast_jev.fast_jev_plugin_outdated(pin)) is False
    assert run(fast_jev.fast_jev_plugin_outdated(None)) is False
    cmd = run(fast_jev.fast_jev_update_command("/h/claude-home"))
    assert cmd.startswith("CLAUDE_CONFIG_DIR=/h/claude-home claude plugin marketplace update fast-jev-compaction")
    assert "; CLAUDE_CONFIG_DIR=/h/claude-home claude plugin update fast-jev-compaction@fast-jev-compaction" in cmd
    assert "install" not in cmd


def test_fast_jev_home_settings_merges_the_plugin_declaration_and_keeps_the_rest() -> None:
    """借りた家の settings.json に plugin の宣言を合流させる(純関数・冪等・他の欄は保つ・壊れた本文は {} から)。"""
    merged = json.loads(run(fast_jev.fast_jev_home_settings(json.dumps({"permissions": {"defaultMode": "auto"}, "env": {"X": "1"}}), "/run/typesafe/key", "/h/.local/state/fast-jev-compaction")))
    assert merged["permissions"] == {"defaultMode": "auto"}
    assert merged["env"] == {"X": "1", "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}
    assert merged["enabledPlugins"] == {fast_jev.FAST_JEV_PLUGIN_ID: True}
    assert merged["extraKnownMarketplaces"][fast_jev.FAST_JEV_MARKETPLACE_NAME]["source"]["url"] == fast_jev.FAST_JEV_MARKETPLACE_URL
    assert merged["pluginConfigs"][fast_jev.FAST_JEV_PLUGIN_ID]["options"] == {"cacheTtlMinutes": 60, "apiKeyFile": "/run/typesafe/key", "stateDir": "/h/.local/state/fast-jev-compaction"}
    assert run(fast_jev.fast_jev_compaction_enabled(json.dumps(merged))) is True
    # 冪等
    assert json.loads(run(fast_jev.fast_jev_home_settings(json.dumps(merged), "/run/typesafe/key", "/h/.local/state/fast-jev-compaction"))) == merged
    # 壊れた本文・不在
    assert run(fast_jev.fast_jev_compaction_enabled(run(fast_jev.fast_jev_home_settings("not json", "/k", "/s")))) is True
    assert run(fast_jev.fast_jev_compaction_enabled(run(fast_jev.fast_jev_home_settings(None, "/k", "/s")))) is True
    # 状態 file の置き場は家の下の持ち越される場所(pod の StatefulSet の PVC が持つ ~/.local/state の中)
    assert run(fast_jev.fast_jev_state_dir("/home/kento")) == "/home/kento/.local/state/fast-jev-compaction"
    assert run(fast_jev.fast_jev_state_dir("/home/kento/")) == "/home/kento/.local/state/fast-jev-compaction"
    # 据える命令は家を名乗り、marketplace の登録の失敗を無視して install に進む
    cmd = run(fast_jev.fast_jev_install_command("/h/claude-home"))
    assert cmd.startswith("CLAUDE_CONFIG_DIR=/h/claude-home claude plugin marketplace add ")
    assert "; CLAUDE_CONFIG_DIR=/h/claude-home claude plugin install fast-jev-compaction@fast-jev-compaction --scope user" in cmd


def test_host_headless_launch_installs_the_compaction_plugin_into_the_borrowed_home_only_when_the_daemon_names_a_key_file(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pod だけの腕(2026-09-22): daemon の env FAST_JEV_COMPACTION_API_KEY_FILE が実在する file を名乗る時だけ、
    起動の前に `claude plugin marketplace add` + `install` を撃ち、家の settings.json に options(apiKeyFile /
    cacheTtlMinutes)と env(function hooks)を合流させる。名乗りが無い(Mac)/ file が無い時は何もしない。
    鍵の値は読まない(settings に載るのは path だけ)。"""
    log = headless_host.root / "argv.log"
    headless_host.stub_env["DOEFF_HEADLESS_STUB_ARGV_LOG"] = str(log)  # 手番の process(session_env 経由)
    monkeypatch.setenv("DOEFF_HEADLESS_STUB_ARGV_LOG", str(log))  # 据える命令(ProcRun = daemon の env)
    home = headless_host.root / "claude-home"
    # 名乗りなし: 何も起きない
    monkeypatch.delenv(fast_jev.FAST_JEV_KEY_FILE_ENV, raising=False)
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-plug-off", "claude"))
    _wait_turn_end(headless_host, "h-plug-off")
    assert not (home / "settings.json").exists() or not run(fast_jev.fast_jev_compaction_enabled((home / "settings.json").read_text()))
    assert "plugin install" not in log.read_text()
    # file が無い名乗り: 何も起きない(warning のみ)
    monkeypatch.setenv(fast_jev.FAST_JEV_KEY_FILE_ENV, str(headless_host.root / "missing-key"))
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-plug-missing", "claude"))
    _wait_turn_end(headless_host, "h-plug-missing")
    assert "plugin install" not in log.read_text()
    # 実在する鍵の file: install が走り、settings.json が効く形になる(値は載らない)
    key = headless_host.root / "typesafe-key"
    key.write_text("secret-value\n")
    monkeypatch.setenv(fast_jev.FAST_JEV_KEY_FILE_ENV, str(key))
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-plug-on", "claude"))
    _wait_turn_end(headless_host, "h-plug-on")
    lines = log.read_text().splitlines()
    installs = [i for i, line in enumerate(lines) if line.startswith("plugin install fast-jev-compaction@fast-jev-compaction")]
    first_turn = [i for i, line in enumerate(lines) if "--session-id" in line and "h-plug-on" not in line]
    assert len(installs) == 1, lines
    assert any(line.startswith("plugin marketplace add") for line in lines), lines
    text = (home / "settings.json").read_text()
    assert run(fast_jev.fast_jev_compaction_enabled(text)) is True
    assert "secret-value" not in text
    assert json.loads(text)["pluginConfigs"][fast_jev.FAST_JEV_PLUGIN_ID]["options"]["apiKeyFile"] == str(key)
    # 2 度目の起動は据え直さない(冪等)
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-plug-again", "claude"))
    _wait_turn_end(headless_host, "h-plug-again")
    assert sum(1 for line in log.read_text().splitlines() if line.startswith("plugin install")) == 1
    # 据え済みだが options が古い形の家(PVC で持ち越された・stateDir なし): install は撃たず宣言へ揃える
    stale = json.loads((home / "settings.json").read_text())
    del stale["pluginConfigs"][fast_jev.FAST_JEV_PLUGIN_ID]["options"]["stateDir"]
    (home / "settings.json").write_text(json.dumps(stale))
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-plug-reconcile", "claude"))
    _wait_turn_end(headless_host, "h-plug-reconcile")
    assert sum(1 for line in log.read_text().splitlines() if line.startswith("plugin install")) == 1
    options = json.loads((home / "settings.json").read_text())["pluginConfigs"][fast_jev.FAST_JEV_PLUGIN_ID]["options"]
    assert options["stateDir"].endswith("/.local/state/fast-jev-compaction"), options
    assert options["apiKeyFile"] == str(key)
    # 据え済みの家の plugin が pin より古い(pod の PVC で持ち越された 0.4.6 など): install は撃たず update を 1 回撃つ
    assert "plugin update" not in log.read_text()
    plugin_json = home / "plugins/marketplaces/fast-jev-compaction/.claude-plugin/plugin.json"
    plugin_json.parent.mkdir(parents=True, exist_ok=True)
    plugin_json.write_text(json.dumps({"name": "fast-jev-compaction", "version": "0.4.6"}))
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-plug-outdated", "claude"))
    _wait_turn_end(headless_host, "h-plug-outdated")
    lines = log.read_text().splitlines()
    assert sum(1 for line in lines if line.startswith("plugin update fast-jev-compaction@fast-jev-compaction")) == 1, lines
    assert any(line.startswith("plugin marketplace update fast-jev-compaction") for line in lines), lines
    assert sum(1 for line in lines if line.startswith("plugin install")) == 1
    # 版が pin と同じ家: update は撃たない(冪等)
    plugin_json.write_text(json.dumps({"name": "fast-jev-compaction", "version": fast_jev.FAST_JEV_PLUGIN_VERSION}))
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-plug-current", "claude"))
    _wait_turn_end(headless_host, "h-plug-current")
    assert sum(1 for line in log.read_text().splitlines() if line.startswith("plugin update")) == 1
    for sid in ("h-plug-off", "h-plug-missing", "h-plug-on", "h-plug-again", "h-plug-reconcile", "h-plug-outdated", "h-plug-current"):
        headless_host.ok("session.cleanup", {"session_id": sid})


def test_otel_home_settings_merges_the_managed_env_and_keeps_the_rest() -> None:
    """借りた家の settings.json に OTel の env を合流させる(純関数・冪等・所有外の env と他の欄は保つ)。
    agora.tenant=personal を名乗るのは tenant が personal の時だけ(会社の行が個人用へ落ちる経路を作らない)。"""
    before = json.dumps({"permissions": {"defaultMode": "auto"},
                         "env": {"X": "1", "OTEL_LOGS_EXPORT_INTERVAL": "1", "OTEL_METRICS_EXPORTER": "old"}})
    merged = json.loads(run(otel_telemetry.otel_home_settings(before, "http://c:4318", "personal", "agentd-pool-0")))
    assert merged["permissions"] == {"defaultMode": "auto"}
    assert merged["env"]["X"] == "1"
    assert merged["env"]["OTEL_METRICS_EXPORTER"] == "otlp"
    assert merged["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://c:4318"
    assert merged["env"]["OTEL_RESOURCE_ATTRIBUTES"] == "agora.tenant=personal,agora.host=agentd-pool-0,agora.runner=agentd"
    assert "OTEL_LOG_RAW_API_BODIES" not in merged["env"]
    assert run(otel_telemetry.otel_home_settings(json.dumps(merged, indent=2, ensure_ascii=False), "http://c:4318", "personal", "agentd-pool-0")) == json.dumps(merged, indent=2, ensure_ascii=False)
    for tenant in ("", "company"):
        attrs = json.loads(run(otel_telemetry.otel_home_settings(None, "http://c:4318", tenant, "h")))["env"]["OTEL_RESOURCE_ATTRIBUTES"]
        assert "agora.tenant" not in attrs
    assert json.loads(run(otel_telemetry.otel_home_settings("not json", "http://c:4318", "personal", "")))["env"]["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"


def test_host_headless_launch_writes_otel_env_into_the_borrowed_home_only_when_the_daemon_names_an_endpoint(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手番の CLI は機体の env を継がないので、daemon の env CLAUDE_OTEL_ENDPOINT が在る時だけ、起動の前に
    借りた家の settings.json の env へ OTel の組を書く。名乗りが無い機体では何も書かない。"""
    home = headless_host.root / "claude-home"
    monkeypatch.delenv(otel_telemetry.OTEL_ENDPOINT_ENV, raising=False)
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-otel-off", "claude"))
    _wait_turn_end(headless_host, "h-otel-off")
    settings = home / "settings.json"
    assert not settings.exists() or "OTEL_EXPORTER_OTLP_ENDPOINT" not in settings.read_text()
    monkeypatch.setenv(otel_telemetry.OTEL_ENDPOINT_ENV, "http://collector:4318")
    monkeypatch.setenv(otel_telemetry.OTEL_TENANT_ENV, "personal")
    monkeypatch.setenv(otel_telemetry.OTEL_HOST_ENV, "agentd-pool-1")
    headless_host.ok("session.launch", _launch_params(headless_host.root, "h-otel-on", "claude"))
    _wait_turn_end(headless_host, "h-otel-on")
    env = json.loads(settings.read_text())["env"]
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector:4318"
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "agora.tenant=personal,agora.host=agentd-pool-1,agora.runner=agentd"


def test_host_headless_events_go_to_the_outbox_and_are_read_through_the_host(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-DOE-AGENTS-012 R-headless-events-go-to-the-tiered-store / R-headless-events-are-read-through-the-host:
    置き場が送り待ちの表の host(pod の本番)は、子の stdout / stderr の行を 1 つも file に書かない。実況の読み
    (session.events_since / session.events_head)と capture は同じ置き場から読む — 呼び手は置き場を知らない。"""
    registry = HeadlessRegistry(OutboxEventStore(headless_host.actor.submit))
    monkeypatch.setattr(host, "HEADLESS_REGISTRY", registry)
    try:
        launched = headless_host.ok("session.launch", _launch_params(headless_host.root, "h-ev", "claude"))
        assert isinstance(launched, dict)
        locator = _text(_obj(launched, "backend_ref"), "events_path")
        _wait_turn_end(headless_host, "h-ev")
        assert not Path(locator).exists()
        assert not Path(locator + ".stderr").exists()
        read = headless_host.ok("session.events_since", {"locator": locator, "cursor": 0, "session_id": "h-ev"})
        assert isinstance(read, dict)
        text = _text(read, "text")
        kinds = [json.loads(line).get("type") for line in text.splitlines()]
        assert "result" in kinds, kinds
        head = headless_host.ok("session.events_head", {"locator": locator, "session_id": "h-ev"})
        assert isinstance(head, dict)
        assert head["cursor"] == read["cursor"]
        again = headless_host.ok(
            "session.events_since", {"locator": locator, "cursor": read["cursor"], "session_id": "h-ev"}
        )
        assert again == {"text": "", "cursor": read["cursor"]}
        captured = headless_host.ok("session.capture", {"session_id": "h-ev", "lines": 50})
        assert isinstance(captured, dict) and "\"type\": \"result\"" in _text(captured, "text")
        headless_host.ok("session.cleanup", {"session_id": "h-ev"})
    finally:
        registry.kill_all()
