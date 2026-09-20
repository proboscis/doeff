"""providerキャッシュの時刻を、プロセスや配達の時刻から推測しない。"""

import importlib
import json

import hy  # noqa: F401
from doeff import run

judgment = importlib.import_module("doeff_agents.sessionhost.acp.judgment")


def response(at: str, mid: str = "m1", *, parent: str | None = None,
             read: int = 100, one_hour: int = 20, five_minutes: int = 0) -> dict:
    return {
        "type": "assistant", "timestamp": at, "parent_tool_use_id": parent,
        "message": {"id": mid, "model": "claude-fable-5-1", "content": [], "usage": {
            "input_tokens": 1, "output_tokens": 1,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": one_hour + five_minutes,
            "cache_creation": {"ephemeral_1h_input_tokens": one_hour,
                               "ephemeral_5m_input_tokens": five_minutes},
        }},
    }


def batch(*records: dict):
    return run(judgment.events_to_deltas(
        "claude", "\n".join(json.dumps(record) for record in records), "job", 0,
        9_999_999, (),
    ))


def test_cache_observation_uses_provider_response_time_not_poll_clock():
    seen = batch(response("1970-01-01T00:01:00Z")).cache_observation
    assert seen == {"responseId": "m1", "at": 60_000, "ttlSeconds": 3600,
                    "model": "claude-fable-5-1", "cacheRead": 100, "cacheWrite": 20}


def test_child_request_and_result_wrapper_cannot_refresh_main_cache():
    seen = batch(response("1970-01-01T00:01:00Z"),
                 response("1970-01-01T00:59:00Z", "child", parent="tool-1"),
                 {"type": "result", "timestamp": "1970-01-01T01:00:00Z",
                  "usage": {"cache_read_input_tokens": 10000}}).cache_observation
    assert seen["responseId"] == "m1"
    assert seen["at"] == 60_000


def test_same_response_replayed_later_does_not_extend_cache():
    seen = batch(response("1970-01-01T00:01:00Z"),
                 response("1970-01-01T00:59:00Z")).cache_observation
    assert seen["at"] == 60_000


def test_last_main_response_carries_observed_ttl_across_read_only_hits():
    seen = batch(response("1970-01-01T00:01:00Z"),
                 response("1970-01-01T00:55:00Z", "m2", one_hour=0)).cache_observation
    assert seen["responseId"] == "m2"
    assert seen["at"] == 3_300_000
    assert seen["ttlSeconds"] == 3600


def test_mixed_ttl_uses_shorter_deadline_and_unknown_ttl_is_not_invented():
    assert batch(response("1970-01-01T00:01:00Z", five_minutes=1)).cache_observation["ttlSeconds"] == 300
    assert batch(response("1970-01-01T00:01:00Z", one_hour=0)).cache_observation["ttlSeconds"] is None


def test_missing_or_invalid_timestamp_does_not_become_the_poll_time():
    for at in ("", "not-a-time", "1970-01-01T00:01:00"):
        assert batch(response(at)).cache_observation is None


def test_cache_metadata_is_persisted_with_turn_end_and_other_status_is_retained():
    cache = {"responseId": "m1", "at": 60_000, "ttlSeconds": 3600,
             "model": "claude-fable-5-1", "cacheRead": 100, "cacheWrite": 20}
    result = run(judgment.turn_record_ended_status({"recordRef": "record:c/stream"}, None, (), cache))
    assert result["cacheObservation"] == cache
    assert result["recordRef"] == "record:c/stream"
    assert result["state"] == "ended"
