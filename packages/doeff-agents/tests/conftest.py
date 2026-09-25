"""doeff-agents テストの共有配線。

- Hy import hook を有効化する(``*_deftests.hy`` の import に必要)。
- deftest の実行時 interpreter fixture を供給する。
- 偽の ACP の書き込み口が積んだ agentd の書き手の契約の破れを、後片付けで赤にする
  (card acp:kanban-issue:ki-6f222893d6b6)。

責務境界(ADR-DOE-HY-002 R2/R3): deftest params の受け渡しは doeff-hy が、
収集は doeff-adr の pytest plugin が所有し、**実行時 fixture は消費側**
(ここでは doeff-agents)が所有する。この fixture は docs/adr/conftest.py の
参照実装と同じ契約で、第 2 の定義点ではない。

各 ``test_sessionhost_*.py`` は deftest を**包み直さず**そのまま公開する。
包むと ``pytestmark``(skipif / marks / parametrize)が関数の ``__dict__``
ごと落ちるため(ADR-DOE-HY-002 law deftest-params-are-honored:
``params_silently_dropped == 0``)。
"""

import json
from collections.abc import Iterator
from pathlib import Path

import hy  # noqa: F401 — activates Hy import hook
import pytest
from doeff_agents.sessionhost.acp import fake as _fake
from doeff_agents.sessionhost.acp.effects import TURN_RECORD_KIND

#: pin した ACP の契約の写し(contracts.lock の copyPath — 生成物・手で直さない)。
_AGORA_KINDS_COPY = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "agora-kinds.json"


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    """Hy の ``test_*.hy`` をこの dir の中だけで直に集める(doeff-cluster の tests と同じ形)。

    Python の包み直しの file を足さずに Hy の deftest を公開する(agora-redesign #608)。
    ``*_deftests.hy`` は従来どおり ``test_*.py`` が公開する — 名が ``test_`` で始まらない
    ので、ここでは集めない。
    """
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        from doeff_adr.pytest_plugin import DoeffAdrHyFile

        return DoeffAdrHyFile.from_parent(parent, path=file_path)
    return None


@pytest.fixture
def doeff_interpreter(request: pytest.FixtureRequest):
    """deftest を走らせる実行時 interpreter(ADR-DOE-HY-002 R3 の参照実装と同じ形)。

    ``:env`` は reader handler 経由で必ず反映する(黙って無視しない)。未対応の
    params は hard fail させる — 黙って無視すると宣言が効かないまま green になる
    (R2)。
    """
    if "doeff_interpreter_name" in request.fixturenames:
        raise NotImplementedError(
            "deftest :interpreters (doeff_interpreter_name) is not wired in "
            "doeff-agents — ADR-DOE-HY-002 R2 forbids silently ignoring it. "
            "Wire the named interpreter stack here before declaring :interpreters."
        )

    def run_program(program, *, env=None):
        from doeff import run

        if env:
            from doeff_core_effects.handlers import reader

            program = reader(dict(env))(program)
        return run(program)

    return run_program


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
