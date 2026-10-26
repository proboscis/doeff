"""The replay-stable session id rule (internal to doeff-agents).

``AgentSpec.session_id`` is the public reading of it; doeff-conductor derives
its own effects' ids by the same rule so a replayed launch names the session it
named before.
"""


def deterministic_session_id(*, run_id: str, node_id: str, attempt: int) -> str:
    """Derive the stable session id required for replay-safe launches."""
    raw = f"{run_id}-{node_id}-{attempt}"
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in raw)
