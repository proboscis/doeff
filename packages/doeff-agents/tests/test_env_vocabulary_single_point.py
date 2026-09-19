"""agent 境界で禁じる env の語彙は policy.hy の 1 点(card acp:kanban-issue:ki-2a061da56ca9).

3 層(受理 policy / spawn substrate / shell の起動)が同じ「運ばせない名」を
別々の literal で持っていたため、3 つとも中身が違っていた。定義を policy へ
寄せ、層は「どの集合を禁じるか」を名指すだけにする。

針は行の字面でなく **名前の集合の関係** を撃つ(ADR 012 の 2026-09-19 の訓)。
"""

from __future__ import annotations

import hy  # noqa: F401 -- installs the .hy import hook
from doeff_agents import shell
from doeff_agents.sessionhost import policy, substrate


def test_layer_sets_are_derived_from_policy() -> None:
    """層ごとの名簿は policy の語彙の合成ちょうど(literal の写しが無い)。"""
    provider_auth = set(policy.PROVIDER_AUTH_ENV_KEYS)
    provider_routing = set(policy.PROVIDER_ROUTING_ENV_KEYS)
    turn_auth = set(policy.TURN_AUTH_ENV_KEYS)

    # spawn は最後の砦 — provider の鍵ちょうど(手番の札はわざと通す)。
    assert set(substrate.FORBIDDEN_AGENT_ENV_KEYS) == provider_auth

    # shell は 3 層で最も広い — 鍵 + 宛先の差し替え + 手番の札。
    assert set(shell.FORBIDDEN_AGENT_ENV_KEYS) == provider_auth | provider_routing | turn_auth

    # 語彙は交わらない(同じ名を 2 つの家が持たない = 足す日に迷わない)。
    assert not provider_auth & provider_routing
    assert not provider_auth & turn_auth
    assert not provider_routing & turn_auth


def test_admission_rejects_provider_auth_and_keeps_turn_auth() -> None:
    """受理は provider の鍵と binding 所有の名を全部落とし、手番の札は通す。"""
    forbidden = set(policy.PROVIDER_AUTH_ENV_KEYS) | set(policy.BINDING_OWNED_ENV_KEYS)
    for name in forbidden:
        assert policy.session_env_admission_error({name: "x"}, "session.launch") is not None, name

    # 手番ごとの資格の札は「わざと運ぶ」(policy TURN-AUTH-ENV-KEYS / ADR 012 R5・R30)。
    # 3 集合を素朴に合併すると、ここが死ぬ。
    for name in policy.TURN_AUTH_ENV_KEYS:
        assert policy.session_env_admission_error({name: "x"}, "session.launch") is None, name

    # provider の差し替えの綴りも受理では通す(挙動は反例の分ちょうどに留める)。
    for name in policy.PROVIDER_ROUTING_ENV_KEYS:
        assert policy.session_env_admission_error({name: "x"}, "session.launch") is None, name
