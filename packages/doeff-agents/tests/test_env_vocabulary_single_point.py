"""agent 境界で禁じる env の語彙は agent_env.hy の 1 点(card acp:kanban-issue:ki-2a061da56ca9・#708).

3 層(受理 policy / spawn substrate / shell の起動)が同じ「運ばせない名」を
別々の literal で持っていたため、3 つとも中身が違っていた。定義を 1 点へ
寄せ、層は「どの集合を禁じるか」を名指すだけにする。家は agent_env.hy(session host を
import しない — headless の経路が session host を引きずらない・agora-redesign #708)で、
sessionhost/policy.hy は同じ object を再輸出するだけ。

針は行の字面でなく **名前の集合の関係** を撃つ(ADR 012 の 2026-09-19 の訓)。
"""

from __future__ import annotations

import ast
from pathlib import Path

import hy  # noqa: F401 -- installs the .hy import hook
import hy.models
from doeff_agents import agent_env, shell
from doeff_agents.sessionhost import policy, substrate

VOCABULARY = (
    "PROVIDER_AUTH_ENV_KEYS",
    "PROVIDER_ROUTING_ENV_KEYS",
    "TURN_AUTH_ENV_KEYS",
    "BINDING_OWNED_ENV_KEYS",
    "env_offenders_against",
    "overlay_env_offenders",
    "policy_normalized_env_key",
    "provider_auth_env_offenders",
)


def test_policy_reexports_the_single_home() -> None:
    """policy の名は agent_env の object そのもの(写しではない)。"""
    for name in VOCABULARY:
        assert getattr(policy, name) is getattr(agent_env, name), name


def _imported_modules_of_hy(path: Path) -> set[str]:
    """Hy の file が import / require する module の名(先頭の点も残す)。"""
    names: set[str] = set()
    for form in hy.read_many(path.read_text(encoding="utf-8"), filename=str(path)):
        if isinstance(form, hy.models.Expression) and form and str(form[0]) in {"import", "require"}:
            for item in form[1:]:
                if isinstance(item, hy.models.Symbol):
                    names.add(str(item))
    return names


def test_home_does_not_import_the_session_host() -> None:
    """家は session host を import しない(#708 — shell → 家 の鎖に session host が入らない)。"""
    home = Path(agent_env.__file__)
    imported = _imported_modules_of_hy(home)
    assert not any(name.startswith(("doeff_agents.sessionhost", ".sessionhost")) for name in imported), imported
    shell_imports = {
        node.module or ""
        for node in ast.walk(ast.parse(Path(shell.__file__).read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(ast.parse(Path(shell.__file__).read_text(encoding="utf-8")))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(name.startswith("doeff_agents.sessionhost") for name in shell_imports), shell_imports


def test_layer_sets_are_derived_from_policy() -> None:
    """層ごとの名簿は agent_env の語彙の合成ちょうど(literal の写しが無い)。"""
    provider_auth = set(agent_env.PROVIDER_AUTH_ENV_KEYS)
    provider_routing = set(agent_env.PROVIDER_ROUTING_ENV_KEYS)
    turn_auth = set(agent_env.TURN_AUTH_ENV_KEYS)

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
