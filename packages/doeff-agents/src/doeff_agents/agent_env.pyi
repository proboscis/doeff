"""agent_env.hy の公開面の型(Python の読み手 = shell・検)。

**部分的な stub**(sessionhost/policy.pyi と同じ作り): Python から触る面だけを宣言する。
"""

#: agent の境界で禁じる env の語彙(shell.py と検が名指す・写さずに**名指す**)。
PROVIDER_AUTH_ENV_KEYS: set[str]
PROVIDER_ROUTING_ENV_KEYS: set[str]
TURN_AUTH_ENV_KEYS: set[str]
CLAUDE_TURN_CREDENTIAL_ENV: str
BINDING_OWNED_ENV_KEYS: set[str]

def policy_normalized_env_key(key: str) -> str: ...
def env_offenders_against(env: dict[str, str], names: set[str] | frozenset[str]) -> list[str]: ...
def overlay_env_offenders(session_env: dict[str, str]) -> list[str]: ...
def provider_auth_env_offenders(session_env: dict[str, str]) -> list[str]: ...
