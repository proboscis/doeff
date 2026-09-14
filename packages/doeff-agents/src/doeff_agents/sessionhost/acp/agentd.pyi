"""agentd.hy の公開面の型(共通の品質検査が Hy の投影と突合する・Python の読み手 = runtime.py)。

defk は Program を返す(引数の型 = :pre の契約・実行後の値の型 = :post)。
"""

from doeff import Program
from doeff_agents.sessionhost.acp.effects import (
    AcpRow,
    AgentdSettings,
    AgentdState,
    ArmChoice,
    InFlightJob,
    JobOutcome,
    LaunchPlan,
    LeaseGrant,
    SessionView,
)

def retire_sessions(session_ids: tuple, reason: str) -> Program: ...
def join_tick(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def observe_transcripts(settings: AgentdSettings, views: tuple, sessions: list) -> Program: ...
def observe_profiles(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def observe_held_profiles(
    settings: AgentdSettings, held_rows: tuple, by_name: dict, counts: dict
) -> Program: ...
def end_job_now(
    settings: AgentdSettings,
    row: AcpRow,
    reason_type: str,
    reason: str,
    pending: tuple,
    now_ms: int,
) -> Program: ...
def borrow_lease(plan: LaunchPlan, purpose: str) -> Program: ...
def headline_turns_for(subject: str, reason: str) -> Program: ...
def record_turns_for(settings: AgentdSettings, subject: str) -> Program: ...
def history_for(settings: AgentdSettings, subject: str, exclude: tuple) -> Program: ...
def incarnate(
    settings: AgentdSettings,
    plan: LaunchPlan,
    choice: ArmChoice,
    view: SessionView | None,
    session_id: str,
    lease: LeaseGrant | None,
    bodies: tuple,
    job_id: str,
    subject: str,
    exclude: tuple,
    opener: str | None,
) -> Program: ...
def start_claimed(
    settings: AgentdSettings,
    state: AgentdState,
    row: AcpRow,
    plan: LaunchPlan,
    choice: ArmChoice,
    view: SessionView | None,
    session_id: str,
    now_ms: int,
    opener: str | None,
) -> Program: ...
def claim_job(
    settings: AgentdSettings,
    state: AgentdState,
    rows: tuple,
    row: AcpRow,
    previously_deferred: tuple,
    now_ms: int,
) -> Program: ...
def mail_bodies_by_ref(settings: AgentdSettings, messages: tuple) -> Program: ...
def mail_of(settings: AgentdSettings, row: AcpRow) -> Program: ...
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
    bodies: tuple,
    missing: tuple,
) -> Program: ...
def push_frames(settings: AgentdSettings, job: InFlightJob, frames: tuple) -> Program: ...
def probe_subscribers(
    settings: AgentdSettings, job: InFlightJob, now_ms: int, phase: str
) -> Program: ...
def read_stream(source: str, path: str, offset: int) -> Program: ...
def append_entries(job: InFlightJob, entries: tuple) -> Program: ...
def stream_records(
    settings: AgentdSettings, job: InFlightJob, source: str, path: str, now_ms: int
) -> Program: ...
def capture_frame(settings: AgentdSettings, job: InFlightJob, now_ms: int) -> Program: ...
def ensure_turn_record(settings: AgentdSettings, job: InFlightJob, now_ms: int, force: bool) -> Program: ...
def stream_job(
    settings: AgentdSettings, job: InFlightJob, view: SessionView, now_ms: int
) -> Program: ...
def end_turn_record(job_id: str, usage: dict | None, entries: tuple) -> Program: ...
def drain_stream(
    settings: AgentdSettings, job: InFlightJob, source: str | None, path: str | None, now_ms: int
) -> Program: ...
def turn_batch_of(
    job: InFlightJob, source: str | None, path: str | None, now_ms: int
) -> Program: ...
def settle_record(
    settings: AgentdSettings,
    state: AgentdState,
    job: InFlightJob,
    view: SessionView | None,
    source: str | None,
    path: str | None,
    outcome: JobOutcome,
    step: str,
    now_ms: int,
) -> Program: ...
def finalize_job(
    settings: AgentdSettings,
    state: AgentdState,
    job: InFlightJob,
    view: SessionView,
    source: str | None,
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
def interrupt_job(
    settings: AgentdSettings, state: AgentdState, job: InFlightJob, row: AcpRow, now_ms: int
) -> Program: ...
def record_interrupts_delivered(job: InFlightJob, ids: tuple) -> Program: ...
def deliver_interrupts_of(
    settings: AgentdSettings, job: InFlightJob, row: AcpRow, now_ms: int
) -> Program: ...
def deliver_interrupts(
    settings: AgentdSettings, state: AgentdState, rows: tuple, now_ms: int
) -> Program: ...
def refresh_rows(state: AgentdState, mode: str) -> Program: ...
def withdraw_jobs(
    settings: AgentdSettings, state: AgentdState, rows: tuple, now_ms: int
) -> Program: ...
def receive_bound_jobs(
    settings: AgentdSettings, state: AgentdState, mode: str, now_ms: int
) -> Program: ...
def spool_record_bodies(job: InFlightJob, bodies: tuple) -> Program: ...
def flush_record_spool(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def mark_recorded(
    state: AgentdState, conversation_id: str, stream_id: str, highest: int
) -> Program: ...
def agentd_tick(settings: AgentdSettings, state: AgentdState) -> Program: ...
def close_jobs_for_stop(
    settings: AgentdSettings, state: AgentdState, now_ms: int, reason: str
) -> Program: ...
