"""agentd.hy の公開面の型(共通の品質検査が Hy の投影と突合する・Python の読み手 = runtime.py)。

defk は Program を返す(引数の型 = :pre の契約・実行後の値の型 = :post)。
"""

from doeff import Program
from doeff_agents.sessionhost.acp.effects import (
    AcpRow,
    AgentdSettings,
    AgentdState,
    InFlightJob,
    LaunchPlan,
    LeaseGrant,
    SessionView,
)

def retire_sessions(session_ids: tuple, reason: str) -> Program: ...
def join_tick(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def end_job_now(
    settings: AgentdSettings,
    row: AcpRow,
    reason_type: str,
    reason: str,
    pending: tuple,
    now_ms: int,
) -> Program: ...
def borrow_for(settings: AgentdSettings, plan: LaunchPlan, purpose: str) -> Program: ...
def incarnate(plan: LaunchPlan, charter: dict, arm: str, view: SessionView | None) -> Program: ...
def claim_job(
    settings: AgentdSettings,
    state: AgentdState,
    rows: tuple,
    row: AcpRow,
    previously_deferred: tuple,
    now_ms: int,
) -> Program: ...
def start_offset_of(view: SessionView, arm: str) -> Program: ...
def after_start(
    settings: AgentdSettings,
    state: AgentdState,
    row: AcpRow,
    plan: LaunchPlan,
    view: SessionView,
    lease: LeaseGrant | None,
    arm: str,
    now_ms: int,
) -> Program: ...
def push_frames(settings: AgentdSettings, job: InFlightJob, frames: tuple) -> Program: ...
def probe_subscribers(
    settings: AgentdSettings, job: InFlightJob, now_ms: int, phase: str
) -> Program: ...
def stream_transcript(
    settings: AgentdSettings, job: InFlightJob, path: str, now_ms: int
) -> Program: ...
def capture_frame(settings: AgentdSettings, job: InFlightJob, now_ms: int) -> Program: ...
def stream_job(
    settings: AgentdSettings, job: InFlightJob, view: SessionView, now_ms: int
) -> Program: ...
def end_turn_record(job_id: str, usage: dict | None, entries: tuple) -> Program: ...
def finalize_job(
    settings: AgentdSettings,
    state: AgentdState,
    job: InFlightJob,
    view: SessionView,
    path: str | None,
    step: str,
    now_ms: int,
) -> Program: ...
def fail_missing_arm(
    settings: AgentdSettings,
    job_key: str,
    job_id: str,
    pending: tuple,
    lease_id: str | None,
    now_ms: int,
) -> Program: ...
def settle_known(
    settings: AgentdSettings,
    state: AgentdState,
    job: InFlightJob,
    view: SessionView | None,
    step: str,
    now_ms: int,
) -> Program: ...
def progressed_of(job: InFlightJob, view: SessionView) -> Program: ...
def observe_job(
    settings: AgentdSettings, state: AgentdState, job: InFlightJob, now_ms: int
) -> Program: ...
def recover_job(
    settings: AgentdSettings, state: AgentdState, row: AcpRow, now_ms: int
) -> Program: ...
def withdraw_sessions(settings: AgentdSettings, state: AgentdState, rows: tuple) -> Program: ...
def receive_bound_jobs(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def agentd_tick(settings: AgentdSettings, state: AgentdState) -> Program: ...
