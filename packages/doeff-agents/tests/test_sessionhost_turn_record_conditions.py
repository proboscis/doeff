"""card acp:kanban-issue:ki-6f222893d6b6: 手番の条件を耐久の turn-record へ写す。

手番を落とさない失敗(記憶が書けない・添付を渡せなかった等)の条件は agent-job の行にしか立たず、その行は Ended から
猶予の後に刈られる。agentd は手番の終わりの書きで turn-record の status.conditions へ写す。ここは

* 写しの 1 点(judgment.turn-record-conditions-of)が、どんな入力でも契約の schema と上限を満たすこと(Verification 7)、
* 出所の刻み(conditions-of-runner / conditions-of-binding)と ended の status の組み方、
* 偽の ACP の書き込み口が書き手の契約の破れを名乗り、tests/conftest.py の後片付けがその test を赤にすること(Verification 8)

を検める。腕ごとの経路(記録の service が落ちた手番・試みをまたぐ手番・割り込み・巡回・退役)は
sessionhost_acp_turn_events_deftests.hy と sessionhost_acp_memory_rows_deftests.hy の deftest が撃つ。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_agents.sessionhost.acp import fake, judgment
from doeff_agents.sessionhost.acp.effects import (
    AGENT_JOB_KIND,
    AGENT_JOB_NAMESPACE,
    AGENTD_CONDITION_TYPES,
    TURN_RECORD_CONDITION_TEXT_MAX,
    TURN_RECORD_CONDITIONS_BYTE_BUDGET,
    TURN_RECORD_CONDITIONS_MAX,
    TURN_RECORD_ENTRIES_BYTE_BUDGET,
    TURN_RECORD_KIND,
    TURN_RECORD_RESPONSES_BYTE_BUDGET,
    InFlightJob,
    JSONObject,
)
from jsonschema import Draft202012Validator, ValidationError, validate

from doeff import run

CONTRACT = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "agora-kinds.json"
CONFTEST = Path(__file__).resolve().parent / "conftest.py"


def _at(value: object, *path: str) -> object:
    """契約の文書を path の順にたどる(途中が object でなければ、その段を名乗って落ちる)。"""
    for step in path:
        assert isinstance(value, dict), f"not an object before {step!r}"
        mapping: dict[str, object] = value
        value = mapping[step]
    return value


def _int_at(value: object, *path: str) -> int:
    found = _at(value, *path)
    assert isinstance(found, int), path
    return found


def _object_at(value: object, *path: str) -> JSONObject:
    found = _at(value, *path)
    assert isinstance(found, dict), path
    mapping: JSONObject = found
    return mapping


def _contract() -> JSONObject:
    document: JSONObject = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return document


def _turn_record() -> JSONObject:
    return _object_at(_contract(), "kinds", TURN_RECORD_KIND)


def _status_schema() -> JSONObject:
    return _object_at(_turn_record(), "schema", "properties", "status")


def _contract_breaks(status: dict[str, object]) -> list[str]:
    try:
        validate(status, _status_schema(), cls=Draft202012Validator)
    except ValidationError as error:
        return [f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"]
    return []


@dataclass(frozen=True)
class _Copied:
    """写しの 1 点の結果(写した項・落とした数)。"""

    items: list[JSONObject]
    dropped: int


def _copy(conditions: list[object]) -> _Copied:
    items, dropped = run(judgment.turn_record_conditions_of(tuple(conditions)))
    return _Copied(items=list(items), dropped=dropped)


def _compact_bytes(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8", "surrogatepass"
        )
    )


def _ended_with(items: list[JSONObject], dropped: int) -> dict[str, object]:
    status: dict[str, object] = {"state": "ended"}
    if items:
        status["conditions"] = items
    if dropped:
        status["conditionsDropped"] = dropped
    return status


def _in_flight_job(attempt: int) -> InFlightJob:
    return InFlightJob(
        job_key=f"{AGENT_JOB_NAMESPACE}:{AGENT_JOB_KIND}:j-1",
        job_namespace=AGENT_JOB_NAMESPACE,
        job_id="j-1",
        subject="c-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        session_id="sid-1",
        agent_type="claude",
        node="mac-1",
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
        materials_cover_the_turn=True,
        attempt=attempt,
    )


# ---------------------------------------------------------------- 契約の写しとの突合


def test_the_writer_bounds_and_the_vocabulary_fit_the_contract_copy() -> None:
    """書き手の上限(effects の写し)= 契約 conventions.turnRecordConditions = schema の maxItems / maxLength。行の上限は
    伸びる欄 3 つの予算の和 + 余裕 4,096。agentd の語はすべて契約の type の長さ(1..64)に収まり、InputExpired を含む。"""
    document = _contract()
    bounds = _object_at(document, "conventions", "turnRecordConditions")
    assert (
        _int_at(bounds, "maxItems"),
        _int_at(bounds, "textMax"),
        _int_at(bounds, "byteBudget"),
    ) == (
        TURN_RECORD_CONDITIONS_MAX,
        TURN_RECORD_CONDITION_TEXT_MAX,
        TURN_RECORD_CONDITIONS_BYTE_BUDGET,
    )
    schema = _object_at(_status_schema(), "properties", "conditions")
    assert _int_at(schema, "maxItems") == TURN_RECORD_CONDITIONS_MAX
    properties = _object_at(schema, "items", "properties")
    assert _int_at(properties, "reason", "maxLength") == TURN_RECORD_CONDITION_TEXT_MAX
    assert _int_at(properties, "message", "maxLength") == TURN_RECORD_CONDITION_TEXT_MAX
    shortest = _int_at(properties, "type", "minLength")
    longest = _int_at(properties, "type", "maxLength")
    assert all(shortest <= len(word) <= longest for word in AGENTD_CONDITION_TYPES)
    assert "InputExpired" in AGENTD_CONDITION_TYPES
    writers = _object_at(_turn_record(), "declaration", "writers", "status")
    assert writers["conditions"] == ["agentd"]
    assert writers["conditionsDropped"] == ["agentd"]
    # 書き手の予算の和が行の上限を越えない(entries の書き手の上限は契約より小さい — 小さいのは常に安全)。
    budget = _int_at(_turn_record(), "declaration", "statusByteBudget")
    entries_budget = _int_at(document, "conventions", "turnRecordEntries", "byteBudget")
    responses_budget = _int_at(document, "conventions", "turnRecordResponses", "byteBudget")
    assert budget == entries_budget + responses_budget + _int_at(bounds, "byteBudget") + 4096
    assert entries_budget >= TURN_RECORD_ENTRIES_BYTE_BUDGET
    assert responses_budget >= TURN_RECORD_RESPONSES_BYTE_BUDGET


# ---------------------------------------------------------------- 写しの 1 点(Verification 7)

_WORDS = sorted(AGENTD_CONDITION_TYPES)


def _long_japanese(count: int) -> list[object]:
    """長い日本語(5,000 字)の reason / message を持つ、互いに違う agentd の項 count 個。"""
    return [
        {
            "type": _WORDS[index % len(_WORDS)],
            "status": "True",
            "reason": "記憶の本文を記録の service へ書けなかった。" * 250,
            "message": "あ" * 5000,
            "attempt": 1 + index // len(_WORDS),
            "at": index,
        }
        for index in range(count)
    ]


_HOSTILE: list[object] = [
    {"type": "Unschedulable", "status": "True", "reason": "placement word — another writer"},
    {"type": "DeliveryHeld", "status": "True", "reason": "messaging word — another writer"},
    {"type": "AgentMemoryUnwritable", "status": "True", "reason": "bool attempt", "attempt": True},
    {"type": "AttachmentIgnored", "status": "True", "reason": "negative at", "at": -5},
    {"type": "AgentSettingIgnored", "status": "Maybe", "reason": "status outside the three words"},
    {"type": "SessionLost", "status": "True", "reason": "attempt 0", "attempt": 0},
    "not an object",
    {"status": "True", "reason": "no type"},
    {"type": 7, "status": "True"},
    {"type": "HostDrained", "status": "True", "reason": 42, "message": None},
    {"type": "InputExpired", "status": "True", "reason": "lone surrogate \ud800 in the text"},
    {"type": "Interrupted", "status": "True", "reason": "same item twice", "attempt": 2},
    {"type": "Interrupted", "status": "True", "reason": "same item twice", "attempt": 2},
]


@pytest.mark.parametrize(
    ("label", "conditions", "eligible"),
    [
        ("long-japanese-40", _long_japanese(40), 40),
        ("hostile-fields", _HOSTILE, 7),
        ("hostile-then-long", _HOSTILE + _long_japanese(12), 19),
        ("empty", [], 0),
    ],
)
def test_the_copy_always_meets_the_contract_and_names_what_it_dropped(
    label: str, conditions: list[object], eligible: int
) -> None:
    """意地の悪い入力(長い日本語・40 件・他の書き手の語・bool / 0 の attempt・負の at・語彙の外の status・dict でない項・
    対の無い surrogate・同じ項の重複)でも、写しは契約の schema と byte の上限と件数の上限を満たし、写した数 + 落とした数 =
    写す資格のある(agentd の語の・互いに違う)項の数。"""
    copied = _copy(conditions)
    items, dropped = copied.items, copied.dropped
    broken = _contract_breaks(_ended_with(items, dropped))
    assert broken == [], (label, broken)
    assert _compact_bytes(items) <= TURN_RECORD_CONDITIONS_BYTE_BUDGET, label
    assert len(items) <= TURN_RECORD_CONDITIONS_MAX, label
    assert all(item["type"] in AGENTD_CONDITION_TYPES for item in items), label
    assert len(items) + dropped == eligible, (label, len(items), dropped)
    if label == "long-japanese-40":
        assert dropped > 0, "上限を越える入力で落とした数を名乗らない"
        reasons = [item["reason"] for item in items]
        assert all(isinstance(r, str) and len(r) == TURN_RECORD_CONDITION_TEXT_MAX for r in reasons)


def test_the_copy_normalizes_each_field_the_contract_bounds() -> None:
    """項ごとの正規化: 他の書き手の語と dict でない項は写さない・status は 3 語へ(外は Unknown)・bool / 0 の attempt と負の
    at は落とす(0 の at は保つ)・文字列でない reason は落とす・同じ項は 1 つ。"""
    copied = _copy(_HOSTILE + [{"type": "LaunchFailed", "status": "False", "at": 0, "attempt": 3}])
    assert copied.dropped == 0
    by_type = {item["type"]: item for item in copied.items}
    assert "Unschedulable" not in by_type
    assert "DeliveryHeld" not in by_type
    assert by_type["AgentMemoryUnwritable"] == {
        "type": "AgentMemoryUnwritable",
        "status": "True",
        "reason": "bool attempt",
    }
    assert by_type["AttachmentIgnored"] == {
        "type": "AttachmentIgnored",
        "status": "True",
        "reason": "negative at",
    }
    assert by_type["AgentSettingIgnored"]["status"] == "Unknown"
    assert "attempt" not in by_type["SessionLost"]
    assert by_type["HostDrained"] == {"type": "HostDrained", "status": "True"}
    assert by_type["LaunchFailed"] == {
        "type": "LaunchFailed",
        "status": "False",
        "at": 0,
        "attempt": 3,
    }
    assert [item["type"] for item in copied.items].count("Interrupted") == 1


def test_the_ended_status_places_the_copy_and_nothing_else() -> None:
    """ended の status: 写す項が在れば conditions、落とした項が在れば conditionsDropped。材料が空なら欄を書かない —
    行に前の値が在っても持ち越さない(欄は渡された材料ちょうど)。"""
    soft: JSONObject = {
        "type": "AgentMemoryUnwritable",
        "status": "True",
        "reason": "unreachable",
        "attempt": 1,
    }
    ended = run(judgment.turn_record_ended_status({"state": "running"}, None, (), (soft,)))
    assert ended["conditions"] == [soft]
    assert "conditionsDropped" not in ended
    stale: JSONObject = {"state": "running", "conditions": [soft], "conditionsDropped": 3}
    empty = run(judgment.turn_record_ended_status(stale, None, (), ()))
    assert "conditions" not in empty
    assert "conditionsDropped" not in empty
    flood = tuple(_long_japanese(40))
    over = run(judgment.turn_record_ended_status({"state": "running"}, None, (), flood))
    assert over["conditionsDropped"] == 40 - len(over["conditions"])


# ---------------------------------------------------------------- 出所の刻み


def test_provenance_names_the_attempt_that_raised_the_condition_and_keeps_a_named_one() -> None:
    """runner の条件は InFlightJob.attempt、runner の無い書きの条件は行の binding.attempt(欄が無ければ 1)を名乗る。
    項が既に名乗る attempt(ProviderLimit・CredentialLeaseHeld)は上書きしない。"""
    raised: JSONObject = {"type": "AttachmentIgnored", "status": "True", "reason": "x"}
    named: JSONObject = {"type": "ProviderLimit", "status": "True", "attempt": 1}
    stamped = run(judgment.conditions_of_runner((raised, named), _in_flight_job(3)))
    assert [item["attempt"] for item in stamped] == [3, 1]
    assert raised == {"type": "AttachmentIgnored", "status": "True", "reason": "x"}, (
        "元の項を書き換えた"
    )
    bound = run(judgment.conditions_of_binding((raised,), {"binding": {"attempt": 2}}))
    assert bound[0]["attempt"] == 2
    unbound = run(judgment.conditions_of_binding((raised,), {}))
    assert unbound[0]["attempt"] == 1


# ---------------------------------------------------------------- 偽の ACP の書き手の契約(Verification 8)


def _job_status(*conditions: JSONObject) -> JSONObject:
    return {"phase": "Running", "conditions": list(conditions)}


def test_the_fake_acp_names_each_break_of_the_writer_contract() -> None:
    """書き込み口の判定(fake.writer_contract_violations): (a) 語彙の外の語 (b) 試みの番号の無い項 (c) 追記の書きの条件
    (d) turn-record の schema と statusByteBudget。書く前から行に在る項は問わない(他の書き手の項・前の版の項)。"""
    contract = fake.TurnRecordStatusContract(
        schema=_status_schema(),
        byte_budget=_int_at(_turn_record(), "declaration", "statusByteBudget"),
    )
    key = f"{AGENT_JOB_NAMESPACE}:{AGENT_JOB_KIND}:j-1"
    placement: JSONObject = {"type": "Unschedulable", "status": "True", "reason": "placed"}
    good: JSONObject = {
        "type": "AgentMemoryUnwritable",
        "status": "True",
        "reason": "x",
        "attempt": 1,
    }
    assert (
        fake.writer_contract_violations(
            AGENT_JOB_KIND, key, _job_status(placement), _job_status(placement, good), contract
        )
        == []
    )
    outside: JSONObject = {"type": "NotAnAgentdWord", "status": "True", "attempt": 1}
    (named,) = fake.writer_contract_violations(
        AGENT_JOB_KIND, key, _job_status(), _job_status(outside), contract
    )
    assert "'NotAnAgentdWord' outside ConditionType" in named
    unstamped: JSONObject = {"type": "AgentMemoryUnwritable", "status": "True", "reason": "x"}
    (bare,) = fake.writer_contract_violations(
        AGENT_JOB_KIND, key, _job_status(), _job_status(unstamped), contract
    )
    assert "without its attempt" in bare
    record_key = f"default:{TURN_RECORD_KIND}:j-1"
    appended: JSONObject = {"state": "running", "entries": [], "conditions": [good]}
    (running,) = fake.writer_contract_violations(
        TURN_RECORD_KIND, record_key, {"state": "running"}, appended, contract
    )
    assert "not the end" in running
    broken: JSONObject = {
        "state": "ended",
        "conditions": [{"type": "AgentMemoryUnwritable", "status": "Maybe"}],
    }
    assert any(
        "breaks the contract" in line
        for line in fake.writer_contract_violations(
            TURN_RECORD_KIND, record_key, {"state": "running"}, broken, contract
        )
    )
    huge: JSONObject = {
        "state": "ended",
        "transcriptRef": "x" * 1024,
        "entries": [{"seq": n, "at": 0, "kind": "text"} for n in range(3000)],
    }
    assert any(
        "over the contract's statusByteBudget" in line
        for line in fake.writer_contract_violations(
            TURN_RECORD_KIND, record_key, {"state": "running"}, huge, contract
        )
    )
    fine: JSONObject = {"state": "ended", "conditions": [good], "conditionsDropped": 2}
    assert (
        fake.writer_contract_violations(
            TURN_RECORD_KIND, record_key, {"state": "running"}, fine, contract
        )
        == []
    )


_INNER_CONFTEST = """
import importlib.util

_spec = importlib.util.spec_from_file_location("doeff_agents_tests_conftest", {conftest!r})
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_fake_acp_writer_contract = _module._fake_acp_writer_contract
"""

_INNER_TEST = """
import os

from doeff_agents.sessionhost.acp import fake
from doeff_agents.sessionhost.acp.effects import AGENT_JOB_KIND, AGENT_JOB_NAMESPACE, AcpRow, Written

WORD = "AgentMemoryUnwritable"


def test_agentd_adds_a_soft_condition(monkeypatch):
    if os.environ.get("KI6F_DROP_WORD") == WORD:
        monkeypatch.setattr(fake, "AGENTD_CONDITION_TYPES", fake.AGENTD_CONDITION_TYPES - {WORD})
    acp = fake.FakeAcp(births={})
    row = AcpRow(namespace=AGENT_JOB_NAMESPACE, key=f"{AGENT_JOB_NAMESPACE}:{AGENT_JOB_KIND}:j-1", kind=AGENT_JOB_KIND,
                 resource_id="j-1", version="v1", generation=1, created_at_ms=0, labels={}, payload={}, spec={},
                 status={"phase": "Running", "conditions": []})
    acp.put_row(row)
    written = acp._put_status(row, {"phase": "Running", "conditions": [
        {"type": WORD, "status": "True", "reason": "record service unreachable", "attempt": 1}]})
    assert isinstance(written, Written)
"""


def _run_inner(tmp_path: Path, drop: str | None) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir()
    (tmp_path / "conftest.py").write_text(
        _INNER_CONFTEST.format(conftest=str(CONFTEST)), encoding="utf-8"
    )
    (tmp_path / "test_inner.py").write_text(_INNER_TEST, encoding="utf-8")
    env = dict(os.environ)
    env.pop("KI6F_DROP_WORD", None)
    if drop is not None:
        env["KI6F_DROP_WORD"] = drop
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--rootdir", str(tmp_path), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_a_word_left_out_of_the_vocabulary_turns_the_writing_test_red_at_teardown(
    tmp_path: Path,
) -> None:
    """負の対照: 検めの語彙から語を 1 つ外すと、その語を書く test が後片付け(tests/conftest.py の autouse fixture)で
    その語を名乗って赤。正の対照: 語彙のままなら同じ test は緑。"""
    dropped = _run_inner(tmp_path / "dropped", "AgentMemoryUnwritable")
    assert dropped.returncode != 0, dropped.stdout + dropped.stderr
    assert "ERROR at teardown of test_agentd_adds_a_soft_condition" in dropped.stdout, (
        dropped.stdout
    )
    assert "'AgentMemoryUnwritable' outside ConditionType" in dropped.stdout, dropped.stdout
    kept = _run_inner(tmp_path / "kept", None)
    assert kept.returncode == 0, kept.stdout + kept.stderr
    assert "1 passed" in kept.stdout, kept.stdout
