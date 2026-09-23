"""要約の model の既定は、doeff が固定した契約の写しの claude の閉語彙に在る(card acp:kanban-issue:ki-2f90057f43a2 依頼 B7)。

agentd は要約の job の charter.model を自分の既定値(effects.AgentdSettings.summarize_model)で直接書き、
engine の改名の読み替えを通らない(設計の記録 herdr-hud docs/design-checks/lt-6FS5BZ9ZQGKT1D2YAMYQ5CS7W3
design.md §8 R2 — 盲検 A の主反例)。だから要約の model は「operator が決めた要約の model」という
**独立した定義点**で、改名の変更範囲に明示で入る。

この検は値を写さない。撃つのは関係 1 つ: 既定の綴り ∈ docs/contracts/agora-kinds.json(doeff が固定した
ACP の契約の写し)の conventions.agentSettings.agentTypes.claude.models。改名の後に doeff が写しを更新した
時点で赤になり、既定の値の更新を強いる(写しの更新までは旧 id が生きている間だけ要約が旧 id で走る)。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from doeff_agents.sessionhost.acp.effects import AgentdSettings

REPO = Path(__file__).resolve().parents[3]
CONTRACT_COPY = REPO / "docs" / "contracts" / "agora-kinds.json"


def claude_models_of(contract: dict[str, object]) -> list[str]:
    """契約(の写し)の claude の閉語彙 — 形が契約の外なら名指しで落ちる(黙って空にしない)。"""
    conventions = contract["conventions"]
    assert isinstance(conventions, dict), "conventions が object でない"
    settings = conventions["agentSettings"]
    assert isinstance(settings, dict), "agentSettings が object でない"
    agent_types = settings["agentTypes"]
    assert isinstance(agent_types, dict), "agentTypes が object でない"
    claude = agent_types["claude"]
    assert isinstance(claude, dict), "agentTypes.claude が object でない"
    models = claude["models"]
    assert isinstance(models, list) and models and all(isinstance(m, str) for m in models), (
        "agentTypes.claude.models が非空の文字列の配列でない"
    )
    return list(models)


def summarize_model_default() -> str:
    """AgentdSettings の summarize_model の**既定**(dataclass の field の default — 器を組まずに読む)。"""
    field = next(f for f in dataclasses.fields(AgentdSettings) if f.name == "summarize_model")
    assert isinstance(field.default, str) and field.default, "summarize_model の既定が文字列でない"
    return field.default


def summarize_model_is_declared(default: str, models: list[str]) -> bool:
    """純関数: 既定が閉語彙に在るか(検の歯 — 下の負例が同じ 1 点を撃つ)。"""
    return default in models


def test_the_summarize_model_default_is_in_the_pinned_contract_copy() -> None:
    contract = json.loads(CONTRACT_COPY.read_text(encoding="utf-8"))
    models = claude_models_of(contract)
    default = summarize_model_default()
    assert summarize_model_is_declared(default, models), (
        f"要約の model の既定 {default!r} が契約の写しの claude の閉語彙 {models} に無い — "
        "effects.AgentdSettings.summarize_model を閉語彙の綴りへ更新する(改名の変更範囲の 1 点)"
    )


def test_a_renamed_vocabulary_that_drops_the_default_is_refused() -> None:
    """負例: 閉語彙が改名後の形(既定の綴りを含まない)へ動くと同じ 1 点が赤になる。"""
    default = summarize_model_default()
    renamed = [m for m in claude_models_of(json.loads(CONTRACT_COPY.read_text(encoding="utf-8"))) if m != default]
    renamed.append(default + "-renamed-rehearsal")
    assert not summarize_model_is_declared(default, renamed)
