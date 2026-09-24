"""Enable Hy import hook for .hy module loading in tests."""

import json
from collections.abc import Iterator
from pathlib import Path

import hy  # noqa: F401 — activates Hy import hook
import pytest
from doeff_agents.sessionhost.acp import fake as _fake
from doeff_agents.sessionhost.acp.effects import TURN_RECORD_KIND

#: pin した ACP の契約の写し(contracts.lock の copyPath — 生成物・手で直さない)。
_AGORA_KINDS_COPY = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "agora-kinds.json"


def _turn_record_contract() -> _fake.TurnRecordStatusContract:
    """写しの kinds.turn-record から、status の schema と statusByteBudget を取る。"""
    document = json.loads(_AGORA_KINDS_COPY.read_text(encoding="utf-8"))
    kind = document["kinds"][TURN_RECORD_KIND]
    return _fake.TurnRecordStatusContract(
        schema=kind["schema"]["properties"]["status"],
        byte_budget=kind["declaration"]["statusByteBudget"],
    )


_TURN_RECORD_CONTRACT = _turn_record_contract()


@pytest.fixture(autouse=True)
def _fake_acp_writer_contract(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """card acp:kanban-issue:ki-6f222893d6b6: agentd が偽の ACP へ書いた status が書き手の契約
    (fake.writer_contract_violations — 条件の語彙・出所・追記に条件が無い・turn-record の schema と上限)を破ったら、
    その test を後片付けで赤にする(破れを名乗る)。本番の関数では例外にしないので、検めるのはこの書き込み口だけ。"""
    monkeypatch.setattr(_fake.FakeAcp, "turn_record_contract", _TURN_RECORD_CONTRACT)
    _fake.FAKE_ACP_CONTRACT_VIOLATIONS.clear()
    yield
    violations = list(_fake.FAKE_ACP_CONTRACT_VIOLATIONS)
    _fake.FAKE_ACP_CONTRACT_VIOLATIONS.clear()
    assert not violations, "agentd の書き手の契約の破れ(偽の ACP の書き込み口):\n" + "\n".join(
        violations
    )
