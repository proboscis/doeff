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

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import hy  # noqa: F401  # registers the .hy importer
import pytest
from doeff_agents.sessionhost import headless as headless_hy
from doeff_agents.sessionhost import host
from doeff_agents.sessionhost.headless_process import HeadlessRegistry
from doeff_agents.sessionhost.headless_protocol import (
    JSON,
    ClaudeDialogue,
    CodexDialogue,
    CodexPlan,
    HeadlessObservation,
    JSONObject,
    TurnEnded,
    claude_user_line,
    parse_record,
    turn_verdict,
)
from doeff_agents.sessionhost.impls import headless_argv
from doeff_agents.sessionhost.store import StoreActor

STUBS = Path(__file__).parent / "headless_stubs"


# ---------------------------------------------------------------- JSON の読み(検の narrowing の小片)


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
    assert dialogue.one_process_per_turn is False  # 段 8 lane 4x: 温かい process
    # 手番が走る前の割り込みは引き受けない(新しい手番を起こさない)
    assert dialogue.inject("early").accepted is False
    turn = dialogue.turn("hello")
    assert turn.sends == (claude_user_line("hello"),)
    assert _record(turn.sends[0]) == {"type": "user", "message": {"role": "user", "content": "hello"}}
    assert turn.close_stdin is False
    assert dialogue.in_flight is True
    init = dialogue.on_line({"type": "system", "subtype": "init", "session_id": "sid-1"})
    assert init.conversation == {"session_id": "sid-1"}
    assert dialogue.on_line({"type": "assistant", "message": {}}).ended is None
    # 走っている手番への割り込み = 同じ user の行(CLI が次の tool の境界で注入する)
    injected = dialogue.inject("stop that")
    assert injected.accepted is True
    assert injected.sends == (claude_user_line("stop that"),)
    ended = dialogue.on_line({"type": "result", "subtype": "success", "is_error": False})
    assert ended.ended == TurnEnded(ok=True, detail="success")
    assert dialogue.in_flight is False
    assert dialogue.inject("late").accepted is False
    # 次の手番は同じ process へ次の user の行
    assert dialogue.turn("again").close_stdin is False
    failed = dialogue.on_line({"type": "result", "subtype": "error_max_turns", "is_error": True})
    assert failed.ended == TurnEnded(ok=False, detail="error_max_turns")
    assert dialogue.interrupt().signal is True


def _rpc(line: str) -> JSONObject:
    return _record(line)


def test_codex_dialogue_handshake_turn_and_interrupt() -> None:
    dialogue = CodexDialogue(CodexPlan(cwd="/w", model="gpt-5", effort="high"))
    opening = dialogue.opening()
    assert [_rpc(line)["method"] for line in opening] == ["initialize"]
    # prompt は thread が開くまで積む
    assert dialogue.turn("hi").sends == ()
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
    second = dialogue.turn("again")
    assert _rpc(second.sends[0])["method"] == "turn/start"
    assert second.close_stdin is False


def test_codex_dialogue_inject_interrupts_then_starts_the_next_turn_as_one_turn() -> None:
    """段 8 lane 4x: 割り込みの本文 = turn/interrupt → interrupted の turn/completed を手番の終わりと
    報告せず、同じ thread へ本文の turn/start(host から見て手番は 1 つのまま)。"""
    dialogue = CodexDialogue(CodexPlan(cwd="/w"))
    opening = dialogue.opening()
    assert dialogue.inject("early").accepted is False  # thread も turn も無い
    init_reply = dialogue.on_line({"id": _rpc(opening[0])["id"], "result": {}})
    thread_start = _rpc(init_reply.sends[1])
    opened = dialogue.on_line({"id": thread_start["id"], "result": {"thread": {"id": "thr-1"}}})
    assert opened.sends == ()
    assert dialogue.inject("still early").accepted is False  # turn が走っていない
    turn_start = _rpc(dialogue.turn("work").sends[0])
    dialogue.on_line({"id": turn_start["id"], "result": {"turn": {"id": "turn-1"}}})
    injected = dialogue.inject("change course")
    assert injected.accepted is True
    assert [_rpc(line)["method"] for line in injected.sends] == ["turn/interrupt"]
    assert _obj(_rpc(injected.sends[0]), "params") == {"threadId": "thr-1", "turnId": "turn-1"}
    # 2 通目の割り込みは止める合図を重ねず本文を継ぎ足す
    second = dialogue.inject("and this")
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
    assert dialogue.inject("late").accepted is False


def test_codex_dialogue_refuses_unsupported_server_request_and_interrupts() -> None:
    dialogue = CodexDialogue(CodexPlan(cwd="/w", resume_thread_id="thr-old"))
    opening = dialogue.opening()
    init_reply = dialogue.on_line({"id": _rpc(opening[0])["id"], "result": {}})
    assert _rpc(init_reply.sends[1])["method"] == "thread/resume"
    assert _obj(_rpc(init_reply.sends[1]), "params")["threadId"] == "thr-old"
    dialogue.on_line(
        {"id": _rpc(init_reply.sends[1])["id"], "result": {"thread": {"id": "thr-old"}}}
    )
    started = dialogue.turn("go")
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


def test_headless_argv_is_print_mode_with_partial_messages() -> None:
    fresh = headless_argv.build_claude_headless(
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
    resumed = headless_argv.build_claude_headless(
        {"work_dir": "/w", "resume_mode": "resume", "conversation": {"session_id": "sid-1"}}
    )
    resumed_argv = resumed["argv"]
    assert isinstance(resumed_argv, list)
    assert resumed_argv[-2:] == ["--resume", "sid-1"]
    assert "--session-id" not in resumed_argv
    codex = headless_argv.build_codex_headless(
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
    built = headless_argv.build_codex_headless(
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
    turn_start = _rpc(dialogue.turn("hi").sends[0])
    assert turn_start["method"] == "turn/start"
    assert _obj(turn_start, "params")["sandboxPolicy"] == {"type": "dangerFullAccess"}
    # 続きの手番(resume)の thread/resume も同じ方策
    resumed = headless_argv.build_codex_headless(
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


def test_headless_process_claude_turn_writes_events_and_stays_warm(tmp_path: Path) -> None:
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
    assert process.deliver("hello there") is True
    _wait_until(lambda: len(process.peek_records()) >= 5)
    observed = process.observe()
    # 段 8 lane 4x: result の後も process は生きて次の手番を受ける(温かい)
    assert observed.alive is True
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    assert observed.conversation == {"session_id": "sid-1"}
    assert observed.accepts_turn is True
    kinds = [str(record.get("type")) for record in observed.records]
    assert kinds == ["system", "stream_event", "stream_event", "assistant", "result"]
    lines = Path(events).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert json.loads(lines[-1])["type"] == "result"
    assert turn_verdict(observed, True).kind == "turn-ended"
    # 手番の外の割り込みは引き受けない(新しい手番を起こさない)
    assert process.inject("nothing runs") is False
    # 次の手番は同じ process へ次の user の行
    assert process.deliver("again") is True
    _wait_until(lambda: len(Path(events).read_text(encoding="utf-8").splitlines()) >= 10)
    again = process.observe()
    assert again.ended == (TurnEnded(ok=True, detail="success"),)
    assert again.alive is True
    assert len(Path(events).read_text(encoding="utf-8").splitlines()) == 10
    # 降ろす = stdin の EOF で自分で降りる
    registry.kill("s1")
    assert process.alive() is False
    assert process.exit_code() == 0


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
    observed = process.observe()
    assert observed.alive is True
    assert observed.ended == (TurnEnded(ok=True, detail="success"),)
    assert assistant_texts(list(observed.records)) == ["echo: slow work", "interrupted: stop and answer"]
    result = [r for r in observed.records if r.get("type") == "result"][-1]
    assert result["num_turns"] == 2
    # 手番が終わった後は引き受けない
    assert process.inject("too late") is False
    registry.kill("s3")


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


# ---------------------------------------------------------------- 3. host の RPC(backend=headless)


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
        self.counter = 0

    def call(self, method: str, params: JSONObject) -> JSONObject:
        self.counter += 1
        line = json.dumps({"id": self.counter, "method": method, "params": params})
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
    host_under_test = Host(root)
    try:
        yield host_under_test
    finally:
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


def test_host_headless_claude_round_trip_launch_turn_end_send_resume_cleanup(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 替え玉は 2 手番目の result の後に自分で降りる(3 手番目が --resume の起こし直しになる材料)
    monkeypatch.setenv("DOEFF_HEADLESS_STUB_TURNS_BEFORE_EXIT", "2")
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
    # 次の手番: 温かい同じ process へ(段 8 lane 4x)— awaiting が立って turn_ended_at が消える
    pid_before = _obj(ended, "backend_ref")["pid"]
    headless_host.ok(
        "session.send", {"session_id": "h-1", "message": "second turn", "awaiting": True}
    )
    after_send = headless_host.snap("h-1")
    assert after_send["awaiting_response"] is True
    assert not _has(after_send, "turn_ended_at")
    assert _obj(after_send, "backend_ref")["pid"] == pid_before
    assert "--resume" not in _texts(_obj(after_send, "backend_ref"), "argv")
    ended_again = _wait_turn_end(headless_host, "h-1")
    assert _text(ended_again, "status") == "running"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    assert _record(lines[5])["resumed"] is True  # 替え玉は 2 手番目から resumed を名乗る
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
    # process が降りた後(替え玉は 2 手番目の result の後に自分で降りた — SIGINT で止めた・idle で退いた器の
    # 再現)の次の手番は --resume の process を起こし直す。monitor は降りた process を idle と読む(failed にしない)。
    monkeypatch.delenv("DOEFF_HEADLESS_STUB_TURNS_BEFORE_EXIT")
    _pause(0.3)
    headless_host.monitor()
    assert headless_host.snap("h-1")["status"] == "running"
    headless_host.ok(
        "session.send", {"session_id": "h-1", "message": "third turn", "awaiting": True}
    )
    after_resume = headless_host.snap("h-1")
    assert "--resume" in _texts(_obj(after_resume, "backend_ref"), "argv")
    assert _obj(after_resume, "backend_ref")["pid"] != pid_before
    ended_third = _wait_turn_end(headless_host, "h-1")
    assert _text(ended_third, "status") == "running"
    assert len(events_path.read_text(encoding="utf-8").splitlines()) == 15
    # cleanup = 終端
    cleaned = headless_host.ok("session.cleanup", {"session_id": "h-1"})
    assert _text(cleaned, "status") == "stopped"
    assert _has(cleaned, "cleaned_at")


def test_host_headless_claude_interrupt_mode_reaches_the_running_turn(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """段 8 lane 4x: session.send の mode = interrupt は走っている手番へ本文を注入する — 手番は
    1 つのまま(awaiting は触らない)、反応が実況(events)に出てから result。"""
    monkeypatch.setenv("DOEFF_HEADLESS_STUB_DELAY", "5")
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


def test_host_headless_interrupt_keeps_the_session_warm(
    headless_host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOEFF_HEADLESS_STUB_DELAY", "30")
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
    monkeypatch.setenv("DOEFF_HEADLESS_STUB_DELAY", "0")
    headless_host.ok(
        "session.send", {"session_id": "h-2", "message": "after interrupt", "awaiting": True}
    )
    again = _wait_turn_end(headless_host, "h-2")
    assert _text(again, "status") == "running"
    headless_host.ok("session.cancel", {"session_id": "h-2"})
    assert _text(headless_host.snap("h-2"), "status") == "stopped"


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


# ---------------------------------------------------------------- 実 binary の smoke(API を撃たない)


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude binary is not installed here")
def test_real_claude_accepts_the_headless_flags_help_only() -> None:
    built = headless_argv.build_claude_headless(
        {"work_dir": os.getcwd(), "session_hooks": "disabled"}
    )
    argv = built["argv"]
    assert isinstance(argv, list)
    binary = argv[0]
    assert isinstance(binary, str)
    result = subprocess.run(
        [binary, "--help"], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0
    for flag in ("--output-format", "--include-partial-messages", "--session-id", "--resume"):
        assert flag in result.stdout
