"""手番の応答ごとの消費(card acp:kanban-issue:ki-c3ac5832a0bd)— 出力は message_delta の最終値・行へは終わりの書きだけ。"""

import json
from pathlib import Path

from doeff_agents.sessionhost.acp import judgment, response_usage
from doeff_agents.sessionhost.acp.effects import TURN_RECORD_RESPONSES_BYTE_BUDGET, DeltaBatch

from doeff import run

CONTRACT = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "agora-kinds.json"


def usage(output: int, *, read: int = 100, write: int = 20, one_hour: int = 20) -> dict:
    return {
        "input_tokens": 2,
        "output_tokens": output,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": one_hour},
    }


def start(mid: str, *, parent: str | None = None, model: str = "claude-opus-5") -> dict:
    return {
        "type": "stream_event",
        "parent_tool_use_id": parent,
        "event": {"type": "message_start", "message": {"id": mid, "model": model, "usage": usage(5)}},
    }


def line(mid: str, at: str, *, parent: str | None = None, model: str = "claude-opus-5") -> dict:
    # stream-json の assistant の行 — usage.output_tokens は block が出た拍の途中の値(実測 5)。
    return {
        "type": "assistant",
        "timestamp": at,
        "parent_tool_use_id": parent,
        "message": {"id": mid, "model": model, "content": [], "usage": usage(5)},
    }


def delta(output: int, *, parent: str | None = None) -> dict:
    return {
        "type": "stream_event",
        "parent_tool_use_id": parent,
        "event": {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": usage(output)},
    }


def batch(*records: dict) -> DeltaBatch:
    return run(
        judgment.events_to_deltas(
            "claude", "\n".join(json.dumps(record) for record in records), "job", 0, 9_999_999, ()
        )
    )


def test_response_output_is_the_message_delta_final_value_not_the_assistant_line() -> None:
    seen = batch(
        start("m1"), line("m1", "1970-01-01T00:01:00Z"), line("m1", "1970-01-01T00:01:01Z"), delta(1244),
        start("m2"), line("m2", "1970-01-01T00:02:00Z"), delta(267),
    ).responses
    assert seen == (
        {"input": 2, "output": 1244, "cacheWrite": 20, "cacheRead": 100, "cacheWrite5m": 0, "cacheWrite1h": 20,
         "model": "claude-opus-5", "at": 60_000},
        {"input": 2, "output": 267, "cacheWrite": 20, "cacheRead": 100, "cacheWrite5m": 0, "cacheWrite1h": 20,
         "model": "claude-opus-5", "at": 120_000},
    )
    # 合計は応答の和(読みの拍ごとの add-usage は途中の出力 5 + 5 を数えていた)。
    assert run(response_usage.usage_of_responses(seen)) == {
        "input": 4, "output": 1511, "cacheWrite": 40, "cacheRead": 200, "cacheWrite5m": 0, "cacheWrite1h": 40,
    }


def test_subagent_responses_are_marked_and_their_delta_does_not_touch_the_parent() -> None:
    seen = batch(
        start("m1"), line("m1", "1970-01-01T00:01:00Z"),
        start("c1", parent="tool-1"), line("c1", "1970-01-01T00:01:10Z", parent="tool-1"), delta(40, parent="tool-1"),
        delta(900),
    ).responses
    assert [(r["at"], r["output"], r.get("subagent")) for r in seen] == [(60_000, 900, None), (70_000, 40, True)]


def test_responses_without_time_or_synthetic_are_not_invented() -> None:
    seen = batch(
        start("m1"),  # assistant の行(時刻)が 1 度も来ない応答
        line("s1", "1970-01-01T00:01:00Z", model="<synthetic>"),
        line("m2", "1970-01-01T00:02:00Z"),
    ).responses
    assert [r["at"] for r in seen] == [120_000]


def test_status_keeps_the_head_within_the_byte_budget_and_names_the_cut_tail() -> None:
    responses = tuple(
        {"at": 1_790_000_000_000 + i, "model": "claude-opus-5", "input": 2, "output": 1234,
         "cacheWrite": 64642, "cacheRead": 10118, "cacheWrite5m": 0, "cacheWrite1h": 64642}
        for i in range(400)
    )
    shaped = run(response_usage.responses_status_of(responses))
    size = run(response_usage.compact_bytes(shaped))
    assert size <= TURN_RECORD_RESPONSES_BYTE_BUDGET
    kept = len(shaped["items"])
    assert 0 < kept < 400 and shaped["count"] == 400 and shaped["dropped"] == 400 - kept
    assert [item["n"] for item in shaped["items"]] == list(range(1, kept + 1))
    # 1 項足すと上限を越える所で止めている(余った予算で切っていない)。
    assert kept > TURN_RECORD_RESPONSES_BYTE_BUDGET // 200
    small = run(response_usage.responses_status_of(responses[:2]))
    assert "dropped" not in small and [item["n"] for item in small["items"]] == [1, 2]


def test_the_first_response_is_kept_even_when_it_alone_exceeds_the_budget() -> None:
    huge = ({"at": 1, "model": "m" * (TURN_RECORD_RESPONSES_BYTE_BUDGET + 10), "input": 1, "output": 1,
             "cacheWrite": 0, "cacheRead": 0}, {"at": 2, "input": 1, "output": 1, "cacheWrite": 0, "cacheRead": 0})
    shaped = run(response_usage.responses_status_of(huge))
    assert [item["n"] for item in shaped["items"]] == [1] and shaped["dropped"] == 1


def test_ended_status_carries_responses_only_when_given() -> None:
    shaped = {"count": 1, "items": [{"n": 1, "at": 1, "input": 1, "output": 1, "cacheWrite": 0, "cacheRead": 0}]}
    ended = run(judgment.turn_record_ended_status({"state": "running"}, None, (), None, shaped))
    assert ended["state"] == "ended" and ended["responses"] == shaped
    assert "responses" not in run(judgment.turn_record_ended_status({"state": "running"}, None, ()))


def test_budget_and_fields_match_the_contract_copy() -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert document["conventions"]["turnRecordResponses"]["byteBudget"] == TURN_RECORD_RESPONSES_BYTE_BUDGET
    responses = document["kinds"]["turn-record"]["schema"]["properties"]["status"]["properties"]["responses"]
    items = responses["properties"]["items"]["items"]
    assert set(response_usage.RESPONSE_TOKEN_FIELDS) | {"n", "at", "model", "subagent"} == set(items["properties"])
    assert set(items["required"]) == set(response_usage.RESPONSE_REQUIRED_TOKENS) | {"n", "at"}


def test_the_turn_end_write_carries_responses_and_the_usage_is_their_sum() -> None:
    # agentd の手番の終わりの 1 点(turn-batch-of → end-turn-record)を fake の世界で通す。
    from test_sessionhost_acp import HeadlessWorld, _claude_events, bound_job, message

    world = HeadlessWorld()
    world.acp.put_row(message("m-1", "first"))
    world.acp.put_row(bound_job("j-1", inputs=["m-1"], created_at_ms=world.local.now_ms - 400))
    world.tick()
    sid = world.sid("j-1")
    responses = [start("m1"), line("m1", "2026-09-23T00:00:01Z"), delta(1244),
                 start("m2"), line("m2", "2026-09-23T00:00:09Z"), delta(267)]
    world.local.transcripts[f"/events/{sid}.events.jsonl"] = (
        "".join(json.dumps(record) + "\n" for record in responses) + _claude_events(sid, "hello")
    )
    world.tick(advance_ms=1_000)
    world.sessions.finish_turn(sid, world.local.now_ms + 100)
    world.tick(advance_ms=1_000)
    record = world.turn_record("j-1")
    assert record is not None and record.status["state"] == "ended"
    shaped = record.status["responses"]
    assert shaped["count"] == 2 and "dropped" not in shaped
    assert [(item["n"], item["output"]) for item in shaped["items"]] == [(1, 1244), (2, 267)]
    assert record.status["usage"]["output"] == 1244 + 267
