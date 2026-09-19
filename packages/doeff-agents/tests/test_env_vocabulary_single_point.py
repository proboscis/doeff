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
    assert substrate.FORBIDDEN_AGENT_ENV_KEYS == policy.PROVIDER_AUTH_ENV_KEYS

    assert shell.FORBIDDEN_AGENT_ENV_KEYS == (
        set(policy.PROVIDER_AUTH_ENV_KEYS)
        | set(policy.PROVIDER_ROUTING_ENV_KEYS)
        | set(policy.TURN_AUTH_ENV_KEYS)
    )

    # 語彙は交わらない(同じ名を 2 つの家が持たない = 足す日に迷わない)。
    assert not set(policy.PROVIDER_AUTH_ENV_KEYS) & set(policy.PROVIDER_ROUTING_ENV_KEYS)
    assert not set(policy.PROVIDER_AUTH_ENV_KEYS) & set(policy.TURN_AUTH_ENV_KEYS)
    assert not set(policy.PROVIDER_ROUTING_ENV_KEYS) & set(policy.TURN_AUTH_ENV_KEYS)


def test_admission_rejects_provider_auth_and_keeps_turn_auth() -> None:
    """受理は provider の鍵と binding 所有の名を全部落とし、手番の札は通す。"""
    for name in set(policy.PROVIDER_AUTH_ENV_KEYS) | set(policy.BINDING_OWNED_ENV_KEYS):
        assert policy.session_env_admission_error({name: "x"}, "session.launch") is not None, name

    # 手番ごとの資格の札は「わざと運ぶ」(policy TURN-AUTH-ENV-KEYS / ADR 012 R5・R30)。
    # 3 集合を素朴に合併すると、ここが死ぬ。
    for name in policy.TURN_AUTH_ENV_KEYS:
        assert policy.session_env_admission_error({name: "x"}, "session.launch") is None, name

    # provider の差し替えの綴りも受理では通す(挙動は反例の分ちょうどに留める)。
    for name in policy.PROVIDER_ROUTING_ENV_KEYS:
        assert policy.session_env_admission_error({name: "x"}, "session.launch") is None, name
