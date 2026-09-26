"""S1 反例の最小再現(読み取りだけ — source は変えず、表の変更を process の中の dict の書き換えで模す)。

路 = 本番と同じ: policy.make-cause → store.terminal-cause-to-dict(行の永続 JSON)→ wire snapshot →
acp.handlers.session_view_of → acp.judgment.job-outcome-of(agentd の ACP の腕が job の結末を組む 1 点)。
"""
import hy  # noqa: F401
from doeff import run
from doeff_agents.sessionhost import policy, store
from doeff_agents.sessionhost.acp import judgment
from doeff_agents.sessionhost.acp.handlers import session_view_of


def outcome(category: str, status: str):
    cause = policy.make_cause(category, "why", "2026-09-26T00:00:00+00:00")
    wire_cause = store.terminal_cause_to_dict(cause)
    view = session_view_of({
        "session_id": "s", "agent_type": "claude", "status": status, "work_dir": "/w",
        "lifecycle": "run_to_completion", "backend_kind": "headless",
        "terminal_cause": wire_cause,
    })
    out = run(judgment.job_outcome_of(view))
    return wire_cause["retryable"], out.cause, tuple(c["type"] for c in out.conditions)


table = policy.TERMINAL_CAUSE_RETRYABLE
cases = [("context_exhausted", "failed"), ("run_failed", "failed"),
         ("host_drained", "stopped"), ("cancelled", "stopped")]
before = {c: outcome(c, s) for c, s in cases}
# S1 の変更を模す: 表の retryable を反転(M1 の 1 行・M2/M3/M4 も同じ向きに直した前提)。
for c, _ in cases:
    table[c] = not table[c]
after = {c: outcome(c, s) for c, s in cases}
for c, _ in cases:
    print(f"{c:18} before retryable={before[c][0]!s:5} -> {before[c][1]} {before[c][2]}")
    print(f"{'':18} after  retryable={after[c][0]!s:5} -> {after[c][1]} {after[c][2]}")
    print(f"{'':18} job outcome changed: {before[c][1:] != after[c][1:]}")
