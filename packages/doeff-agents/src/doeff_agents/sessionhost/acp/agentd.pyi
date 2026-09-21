"""agentd.hy の公開面の型(共通の品質検査が Hy の投影と突合する・Python の読み手 = runtime.py)。

defk は Program を返す(引数の型 = :pre の契約・実行後の値の型 = :post)。
"""

from doeff import Program
from doeff_agents.sessionhost.acp.effects import (
    AcpRow,
    AgentdSettings,
    AgentdState,
    ArmChoice,
    InFlightCommand,
    DeltaBatch,
    InFlightJob,
    InFlightSummarize,
    SummarizePlan,
    SummaryRegion,
    JobCancel,
    JobOutcome,
    LaunchPlan,
    LeaseGrant,
    SessionView,
)

def retire_sessions(session_ids: tuple, reason: str) -> Program: ...
def join_tick(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def write_node_observations(settings: AgentdSettings, key: str, sessions: list, transcripts: list) -> Program: ...
def lease_heartbeat(settings: AgentdSettings) -> Program: ...
def observe_transcripts(settings: AgentdSettings, views: tuple, sessions: list) -> Program: ...
def observe_pane_seats(sessions: list, note: str) -> Program: ...
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
def record_turns_for(settings: AgentdSettings, subject: str, floor: int | None) -> Program: ...
def history_for(settings: AgentdSettings, subject: str, exclude: tuple) -> Program: ...
def incarnate(
    settings: AgentdSettings,
    plan: LaunchPlan,
    choice: ArmChoice,
    view: SessionView | None,
    session_id: str,
    lease: LeaseGrant | None,
    bodies: tuple,
    carried: tuple,
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
def work_dir_ready(settings: AgentdSettings, row: AcpRow, plan: LaunchPlan, now_ms: int) -> Program: ...
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
    subject: str,
    bodies: tuple,
    carried: tuple,
    missing: tuple,
) -> Program: ...
def push_frames(settings: AgentdSettings, job: InFlightJob, frames: tuple) -> Program: ...
def probe_subscribers(
    settings: AgentdSettings, job: InFlightJob, now_ms: int, phase: str
) -> Program: ...
def read_stream(source: str, path: str, offset: int) -> Program: ...
def append_entries(job: InFlightJob, entries: tuple) -> Program: ...
def stream_push(
    settings: AgentdSettings, job: InFlightJob, source: str, path: str, at: int
) -> Program: ...
def stream_append(settings: AgentdSettings, job: InFlightJob, now_ms: int) -> Program: ...
def stream_records(
    settings: AgentdSettings, job: InFlightJob, source: str, path: str, now_ms: int
) -> Program: ...
def record_interrupt_marks(job: InFlightJob) -> Program: ...
def settle_interrupts(job: InFlightJob, now_ms: int) -> Program: ...
def capture_frame(settings: AgentdSettings, job: InFlightJob, now_ms: int) -> Program: ...
def ensure_turn_record(settings: AgentdSettings, job: InFlightJob, now_ms: int, force: bool) -> Program: ...
def stream_job_read(
    settings: AgentdSettings, job: InFlightJob, source: str | None, path: str | None
) -> Program: ...
def stream_job_watch(
    settings: AgentdSettings, job: InFlightJob, source: str | None, now_ms: int
) -> Program: ...
def stream_job_slow(settings: AgentdSettings, job: InFlightJob, now_ms: int) -> Program: ...
def end_turn_record(
    job_id: str, usage: dict | None, entries: tuple, mark: tuple | None
) -> Program: ...
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
def progressed_of(job: InFlightJob, path: str | None) -> Program: ...
def observe_job_fast(
    settings: AgentdSettings, state: AgentdState, job: InFlightJob, now_ms: int
) -> Program: ...
def observe_job_slow(
    settings: AgentdSettings,
    state: AgentdState,
    job: InFlightJob,
    view: SessionView | None,
    step: str,
    now_ms: int,
) -> Program: ...
def recover_job(
    settings: AgentdSettings, state: AgentdState, row: AcpRow, now_ms: int
) -> Program: ...
def interrupt_job(
    settings: AgentdSettings, state: AgentdState, job: InFlightJob, row: AcpRow, now_ms: int
) -> Program: ...
def delivered_status_with_undeclared(job: InFlightJob, status: dict, ids: tuple) -> Program: ...
def record_interrupts_delivered(job: InFlightJob, ids: tuple) -> Program: ...
def deliver_interrupts_of(
    settings: AgentdSettings, job: InFlightJob, row: AcpRow, now_ms: int
) -> Program: ...
def deliver_interrupts(
    settings: AgentdSettings, state: AgentdState, rows: tuple, now_ms: int
) -> Program: ...
def refresh_rows(state: AgentdState, mode: str) -> Program: ...
# 段 12 lane 12j(agora-redesign #367): 取り消しの 3 段の腕
def acknowledge_cancel(
    settings: AgentdSettings,
    state: AgentdState,
    job: InFlightJob,
    row: AcpRow,
    view: SessionView | None,
    cancel: JobCancel,
    now_ms: int,
) -> Program: ...
def force_cancel(
    settings: AgentdSettings,
    state: AgentdState,
    job: InFlightJob,
    view: SessionView | None,
    cancel: JobCancel,
    now_ms: int,
) -> Program: ...
def cancel_jobs(
    settings: AgentdSettings, state: AgentdState, rows: tuple, now_ms: int
) -> Program: ...
# 段 12 lane 12j(agora-redesign #402): 着かなかった Ended の書き直し
def record_unrecorded_ends(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def withdraw_jobs(
    settings: AgentdSettings, state: AgentdState, rows: tuple, now_ms: int
) -> Program: ...
def receive_bound_jobs(
    settings: AgentdSettings, state: AgentdState, mode: str, now_ms: int
) -> Program: ...
# 段 12(agora-redesign #537 便 1): 走っている turn-record の終状態を読む巡回(level-triggered・memory なし)
def sweep_turn_records(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def spool_record_bodies(job: InFlightJob, bodies: tuple) -> Program: ...
def flush_record_spool(settings: AgentdSettings, state: AgentdState, now_ms: int) -> Program: ...
def mark_recorded(
    state: AgentdState, conversation_id: str, stream_id: str, highest: int
) -> Program: ...
def agentd_tick(settings: AgentdSettings, state: AgentdState) -> Program: ...
def close_jobs_for_stop(
    settings: AgentdSettings, state: AgentdState, now_ms: int, reason: str
) -> Program: ...

# 段 12 lane 12a(agora-redesign #230): verify の命令の腕
def claim_verify_job(
    settings: AgentdSettings, state: AgentdState, row: AcpRow, now_ms: int
) -> Program: ...
def end_command(
    settings: AgentdSettings,
    command: InFlightCommand,
    result: dict | None,
    conditions: tuple,
    now_ms: int,
) -> Program: ...
def observe_command(
    settings: AgentdSettings, state: AgentdState, command: InFlightCommand, now_ms: int
) -> Program: ...
def recover_command(
    settings: AgentdSettings, state: AgentdState, row: AcpRow, now_ms: int
) -> Program: ...
def withdraw_command(
    settings: AgentdSettings, state: AgentdState, command: InFlightCommand, row: AcpRow, now_ms: int
) -> Program: ...

# 段 12 lane 12j(agora-redesign #233): 会話の履歴の段階つき要約(charter.kind = summarize)の腕
def read_summary_region(conversation_id: str, from_seq: int, until: int, budget: int) -> Program: ...
def end_summarize_job(
    settings: AgentdSettings, job_key: str, job_id: str, result: dict | None, conditions: tuple, now_ms: int
) -> Program: ...
def start_summary_region(
    settings: AgentdSettings,
    plan: SummarizePlan,
    region: SummaryRegion,
    paths: dict,
    started_ms: int,
    job_key: str,
    job_namespace: str,
    regions_done: int,
) -> Program: ...
def claim_summarize_job(
    settings: AgentdSettings, state: AgentdState, row: AcpRow, now_ms: int
) -> Program: ...
def finish_summarize(
    settings: AgentdSettings,
    state: AgentdState,
    command: InFlightSummarize,
    result: dict | None,
    conditions: tuple,
    now_ms: int,
) -> Program: ...
def advance_summary_region(
    settings: AgentdSettings, state: AgentdState, command: InFlightSummarize, now_ms: int
) -> Program: ...
def settle_summary_region(
    settings: AgentdSettings, state: AgentdState, command: InFlightSummarize, rc: int, now_ms: int
) -> Program: ...
def observe_summarize(
    settings: AgentdSettings, state: AgentdState, command: InFlightSummarize, now_ms: int
) -> Program: ...
def recover_summarize(
    settings: AgentdSettings, state: AgentdState, row: AcpRow, now_ms: int
) -> Program: ...
def withdraw_summarize(
    settings: AgentdSettings, state: AgentdState, command: InFlightSummarize, row: AcpRow, now_ms: int
) -> Program: ...

# 段 12 lane 12j 便 3(agora-redesign #233): 要約の契機と、再開が読む要約
def summaries_for(settings: AgentdSettings, subject: str) -> Program: ...
def trigger_summarize(settings: AgentdSettings, job: InFlightJob, batch: DeltaBatch, now_ms: int) -> Program: ...
