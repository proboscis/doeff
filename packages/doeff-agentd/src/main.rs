use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use regex::Regex;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const DEFAULT_MONITOR_INTERVAL_MS: u64 = 1000;
const DEFAULT_MAX_RUNNING_SESSIONS: usize = 10;
const LEASE_NAME: &str = "doeff-agentd";
const LEASE_TTL_SECONDS: i64 = 10;
/// Wait up to 30s for sqlite write locks. The default `busy_timeout = 0`
/// causes silent monitor-loop death: with multiple connections (serve
/// thread + monitor thread) writing to a delete-journal database, any
/// momentary write conflict returns `SQLITE_BUSY` immediately, `?`
/// bubbles it out, and the eprintln goes to an orphaned PIPE when
/// agentd runs under launchd. Set globally so every connection retries.
const SQLITE_BUSY_TIMEOUT_MS: u32 = 30_000;
/// Force a running session to `exited` once its last_observed_at is
/// older than this threshold. Guards against tmux probes that hang
/// or DB write paths that silently fail: the watchdog touches only
/// the database, so even if the rest of the monitor pipeline is
/// broken the session can no longer occupy a concurrency slot
/// forever. Real codex sessions refresh `last_observed_at` every
/// monitor_interval (~1s), so 5 minutes is two orders of magnitude
/// above the noise floor.
/// Override with the `DOEFF_AGENTD_STALE_OBSERVATION_SECS` env var.
const STALE_OBSERVATION_THRESHOLD_SECONDS: i64 = 300;

/// Effective stale-observation threshold: the
/// `STALE_OBSERVATION_THRESHOLD_SECONDS` default, overridable at runtime
/// via the `DOEFF_AGENTD_STALE_OBSERVATION_SECS` env var (positive
/// integer seconds). Read at use-site so the conformance suite can
/// compress the watchdog to seconds without a rebuild — a pure
/// testability knob (semantics and default unchanged; recorded in the
/// conformance README knob table).
fn effective_stale_observation_threshold_seconds() -> i64 {
    env_positive_i64("DOEFF_AGENTD_STALE_OBSERVATION_SECS")
        .unwrap_or(STALE_OBSERVATION_THRESHOLD_SECONDS)
}
/// Force a `running` session to `failed` if it has never reached
/// the agent's "active" marker (= still inside startup) for this
/// long.  Distinct from the stale-observation watchdog: the startup
/// spinner ticks the wall-clock every second so the tmux capture
/// keeps changing and `last_observed_at` keeps refreshing — the
/// agent is "live" by every external measure but never actually
/// starts work.  Past incident: an MCP server with an expired
/// refresh token blocked codex's initialisation for 8+ hours on
/// every launch, eventually filling the concurrency cap with
/// sessions that produced no output beyond the startup banner.
/// That incident went uncaught because codex's MCP-startup spinner
/// shows the same "(… • esc to interrupt)" marker as active work,
/// which set `observed_active_at` and DISABLED this watchdog — see
/// `output_has_codex_active_marker`, now fixed to ignore the
/// "Starting MCP servers" phase so a startup hang keeps
/// `observed_active_at` unset and is reaped here.
///
/// Default 60s: codex's normal cold-start (incl. healthy MCP fleet)
/// fits in tens of seconds, so 60s catches a hung MCP server quickly
/// without rip-cording a transiently slow but recoverable launch.
/// Override with the `DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS` env var.
const LAUNCH_TIMEOUT_SECONDS: i64 = 60;

/// Effective launch/MCP-startup timeout: the `LAUNCH_TIMEOUT_SECONDS`
/// default, overridable at runtime via the
/// `DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS` env var (positive integer
/// seconds). Read at use-site so an operator can retune without a
/// rebuild.
fn effective_launch_timeout_seconds() -> i64 {
    env::var("DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS")
        .ok()
        .and_then(|s| s.trim().parse::<i64>().ok())
        .filter(|&v| v > 0)
        .unwrap_or(LAUNCH_TIMEOUT_SECONDS)
}
const LIFECYCLE_RUN_TO_COMPLETION: &str = "run_to_completion";
const LIFECYCLE_INTERACTIVE: &str = "interactive";

/// Default cap on how long `session.await_result` blocks before
/// returning a timeout error.  10 minutes matches the typical upper
/// bound of a single agent turn under `run_to_completion`.
const DEFAULT_AWAIT_TIMEOUT_SECONDS: f64 = 600.0;
/// Lower bound for the await_result timeout.  Below 1s the polling
/// loop has no useful work to do and the connection thrashes.
const MIN_AWAIT_TIMEOUT_SECONDS: f64 = 1.0;
/// Upper bound for the await_result timeout.  Keeps a misbehaving
/// client from parking an agentd thread for an unbounded time.
const MAX_AWAIT_TIMEOUT_SECONDS: f64 = 3600.0;
/// Cadence at which the await loop re-reads the session row.  500ms
/// is well below the monitor loop's own cadence (~1s) so callers see
/// terminal transitions promptly without putting noticeable load on
/// sqlite.
const AWAIT_POLL_INTERVAL_MS: u64 = 500;
/// JSON-RPC error code returned when `session.await_result` exceeds
/// its caller-supplied timeout.  Inside the JSON-RPC 2.0 reserved
/// "server error" range (-32000..-32099).
const RPC_ERR_AWAIT_TIMEOUT: i32 = -32000;
/// JSON-RPC error code returned when `session.await_result` targets a
/// session id that does not exist (or has been deleted during the
/// wait).
const RPC_ERR_NO_SUCH_SESSION: i32 = -32001;
/// JSON-RPC error code returned when `session.report_result` receives a
/// payload that does not satisfy the session's result schema.  The
/// rejection is final for that payload: agentd never re-validates it
/// (ADR 0035 R4).  A later turn-end without a valid result enters the
/// bounded solicitation loop instead (ADR-DOE-AGENTS-002 R3).
const RPC_ERR_RESULT_REJECTED: i32 = -32002;
/// JSON-RPC error code returned when `session.report_result` arrives after
/// the session already reached a terminal status without a result.
const RPC_ERR_ALREADY_TERMINAL: i32 = -32003;
/// MCP protocol version the `report-result-mcp` stdio server advertises
/// when a client omits its own requested version.
const MCP_PROTOCOL_VERSION: &str = "2024-11-05";

/// ADR-DOE-AGENTS-002 R1/R2: how many corrective "call report_result now"
/// solicitations the monitor sends to a contract session that reaches
/// turn-end without a valid reported result, before finalising it as
/// terminal-without-result.  A missing result at turn-end is an
/// OBSERVATION (the agent may simply have stopped talking), not a
/// deterministic failure — the deterministic case is a schema-invalid
/// payload, which is rejected immediately (-32002) and never
/// re-validated.  Override with `--result-solicitations` /
/// `DOEFF_AGENTD_RESULT_SOLICITATIONS`; 0 restores the old
/// fail-immediately behaviour.
const DEFAULT_RESULT_SOLICITATION_LIMIT: u32 = 2;

/// ADR-DOE-AGENTS-002 R5: how long a run_to_completion pane may stay
/// byte-identical (no active-work marker, no idle REPL prompt) before
/// the interactive-prompt watchdog treats it as blocked on an
/// interactive prompt and consults the judge.  Override with
/// `--prompt-stall-secs` / `DOEFF_AGENTD_PROMPT_STALL_SECS`.
const DEFAULT_PROMPT_STALL_SECONDS: i64 = 180;

/// ADR-DOE-AGENTS-002 R5/R7: bounded judge/unblock attempts per session
/// (durable column `prompt_unblock_attempts`).  Exceeding the bound
/// fails the session loudly with `interactive-prompt-blocked` — never
/// an infinite wait.  Override with `--prompt-unblock-attempts` /
/// `DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS`.
const DEFAULT_PROMPT_UNBLOCK_LIMIT: u32 = 3;

/// ADR-DOE-AGENTS-002 R5: default command for the interactive-prompt
/// judge — a small LLM invoked as `sh -c <cmd>` with the judge prompt
/// on stdin, expected to print a single JSON object
/// `{"blocked": bool, "keys": [..], "reason": ".."}`.  Override with
/// `--prompt-judge-cmd` / `DOEFF_AGENTD_PROMPT_JUDGE_CMD`; an empty
/// string disables the judge (the stall watchdog then fails loudly
/// without an unblock attempt, and turn-end solicitation proceeds
/// without menu disambiguation).
// The judge runs through `sh -c`, so the single-quoted settings JSON
// survives word splitting.  disableAllHooks: the judge must not inherit
// the operator's interactive hooks (measured: a Stop hook replaced the
// judge verdict with "Operation stopped by hook").
// The judge is an adjudicator subprocess, not an agent session launch — the
// one-shot form IS the intended physics, hence the inline nosemgrep.
const DEFAULT_PROMPT_JUDGE_CMD: &str =
    "claude -p --settings '{\"disableAllHooks\":true}' --model haiku"; // nosemgrep: doeff-agents-no-claude-print-mode

/// Wall-clock cap on one judge invocation.  The judge runs inside the
/// monitor tick, so a hung judge process must not stall observation of
/// the other sessions indefinitely.
const PROMPT_JUDGE_TIMEOUT_SECONDS: u64 = 45;

/// Upper bound on how many keys one judge verdict may send.  Bounds
/// the damage of a hallucinated key sequence.
const PROMPT_JUDGE_MAX_KEYS: usize = 8;

/// The corrective message the monitor pastes into a contract session
/// that reached turn-end without reporting a result
/// (ADR-DOE-AGENTS-002 R1).
const RESULT_SOLICITATION_MESSAGE: &str =
    "AGENTD RESULT CONTRACT: your turn ended without a report_result call. \
     Call the report_result MCP tool now with a payload that satisfies the \
     declared result schema. Do only that — no other actions, no files.";

#[derive(Debug, Clone)]
struct Config {
    db_path: PathBuf,
    socket_path: PathBuf,
    tmux_bin: String,
    monitor_interval: Duration,
    max_running: usize,
    /// ADR-DOE-AGENTS-002 R2: bound on turn-end result solicitations.
    result_solicitation_limit: u32,
    /// ADR-DOE-AGENTS-002 R5: pane-unchanged threshold for the
    /// interactive-prompt stall watchdog.
    prompt_stall_seconds: i64,
    /// ADR-DOE-AGENTS-002 R5/R7: bound on judge/unblock attempts.
    prompt_unblock_limit: u32,
    /// ADR-DOE-AGENTS-002 R5: the judge command (`sh -c`); None
    /// disables the judge.
    prompt_judge_cmd: Option<String>,
}

#[derive(Debug, Serialize)]
struct LeaseSnapshot {
    lease_name: String,
    owner_pid: i64,
    heartbeat_at: String,
    expires_at: String,
}

#[derive(Debug, Deserialize)]
struct RpcRequest {
    id: Value,
    method: String,
    #[serde(default)]
    params: Value,
}

#[derive(Debug, Serialize, Deserialize)]
struct RpcResponse {
    id: Value,
    ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    /// Structured JSON-RPC 2.0 style error code.  Present only on
    /// failure responses raised through `RpcError`; preserved alongside
    /// the human-readable `error` string for back-compat with existing
    /// clients (notably the Python client which only reads `error`).
    /// New methods such as `session.await_result` use this so callers
    /// can distinguish e.g. "no such session" (-32001) from "timeout"
    /// (-32000) without parsing the error message.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    error_code: Option<i32>,
}

/// Structured error carrying a JSON-RPC 2.0 style error code.  Used by
/// handlers that need to differentiate failure modes on the wire — the
/// dispatch wrapper downcasts the inner `anyhow::Error` and forwards
/// both the code and the message into the response.
#[derive(Debug, Clone)]
struct RpcError {
    code: i32,
    message: String,
}

impl std::fmt::Display for RpcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for RpcError {}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionSnapshot {
    session_id: String,
    session_name: String,
    pane_id: String,
    agent_type: String,
    work_dir: String,
    lifecycle: String,
    status: String,
    backend_kind: String,
    backend_ref: BTreeMap<String, String>,
    started_at: String,
    last_observed_at: Option<String>,
    finished_at: Option<String>,
    cleaned_at: Option<String>,
    pr_url: Option<String>,
    output_snippet: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    terminal_cause: Option<TerminalCause>,
    /// Optional structured-result contract. When set, the monitor
    /// refuses to finalise the session as terminal until the transcript
    /// contains a structured result block that matches the declared
    /// schema. Missing or invalid output triggers an auto-retry up to
    /// `max_retries` times; exhausting retries marks the session as
    /// failed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    expected_result: Option<ExpectedResultSpec>,
    /// How many retries the monitor has issued so far for this session.
    /// Used together with `expected_result.max_retries` to decide
    /// whether the next validation failure triggers another retry or
    /// finalises as failed.
    #[serde(default)]
    retries_used: u32,
    /// Most recent validation reason — surfaced in events so callers
    /// can see *why* the monitor retried (or gave up).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    last_validation_error: Option<String>,
    /// True while the monitor is waiting for the agent to acknowledge
    /// a freshly-sent prompt (initial launch message or a retry).  The
    /// monitor flips this back to false the first time it observes the
    /// agent's "active" marker (e.g. codex's "Working ...").  Without
    /// this latch a freshly-launched interactive agent that is briefly
    /// idle while booting would be misclassified as "done" before it
    /// even processed the message.
    #[serde(default)]
    awaiting_response: bool,
    /// Wall-clock timestamp of the first observation where the agent's
    /// active marker appeared in the tmux capture.  None for sessions
    /// that have not yet completed startup; once set, never cleared.
    ///
    /// The `LAUNCH_TIMEOUT_SECONDS` watchdog uses this to distinguish
    /// "agent is taking a while" from "agent never got past startup":
    /// the latter is the failure mode we hit when a hung MCP server
    /// (e.g. one with an invalid auth token) blocks codex's
    /// initialisation forever.  Without this field every such session
    /// pinned a concurrency slot until manual operator cleanup, since
    /// the existing zombie reaper looks for idle shell — not idle
    /// startup spinner.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    observed_active_at: Option<String>,
    /// The validated result payload, captured from the runtime-managed
    /// transcript result block at the moment the monitor accepted it
    /// (status -> `done`). Stored as the serialized JSON object so
    /// `session.await_result` can return the result after the terminal
    /// pane/worktree has been cleaned up.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    result_payload: Option<String>,
    /// ADR-DOE-AGENTS-002 R2: durable count of "call report_result now"
    /// solicitations sent to this session.  Deliberately NOT the
    /// vestigial `retries_used` (whose wire meaning was the removed
    /// validation re-prompt) and deliberately NOT cleared on daemon
    /// restart — the bound must survive restarts.
    #[serde(default)]
    result_solicitations_used: u32,
    /// ADR-DOE-AGENTS-002 R5: durable count of interactive-prompt judge
    /// invocations (each may send unblock keys).  Survives restart.
    #[serde(default)]
    prompt_unblock_attempts: u32,
    /// Timestamp of the last observed CHANGE of the capture tail —
    /// unlike `last_observed_at`, which refreshes on every successful
    /// capture even when the pane is frozen.  Basis of the "pane
    /// unchanged for T seconds" stall trigger (ADR-DOE-AGENTS-002 R5).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    last_output_change_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum TerminalCauseCategory {
    RateLimited,
    TimedOut,
    Cancelled,
    Lost,
    ProtocolError,
    RunnerUnavailable,
    RunFailed,
    /// ADR-DOE-AGENTS-002 R7: the pane froze on an interactive prompt
    /// and the bounded judge/unblock loop could not clear it.  Internal
    /// / audit detail only — the ACP-facing discriminator is unchanged
    /// (the failure surfaces as `failed` + `last_validation_error`).
    InteractivePromptBlocked,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct TerminalCause {
    category: TerminalCauseCategory,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    reason: Option<String>,
    retryable: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    retry_after_seconds: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    backend_error_code: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exit_code: Option<i32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    signal: Option<String>,
    observed_at: String,
}

/// Contract the launcher attaches to a session to enforce input→output
/// semantics on top of doeff-agentd's existing terminal-detection
/// heuristics.  When set, the agent must deliver its result over the
/// agentd-owned `report_result` MCP tool (see ADR 0035); agentd validates
/// the reported payload against `payload_schema` before the session may
/// enter a terminal `done` state.
///
/// Wire note: older launchers also send `retry_prompt` / `max_retries`.
/// ADR 0035 removed the re-prompt path (a deterministic validation
/// failure is never retried — hard rule 7), so those fields are now
/// ignored.  There is no `deny_unknown_fields`, so they deserialize
/// harmlessly and the wire contract stays backward-compatible.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct ExpectedResultSpec {
    /// The JSON-Schema (a constrained subset agentd enforces — see
    /// `validate_against_schema`) the agent's result must satisfy. This
    /// is the only thing the launcher supplies. agentd owns the result
    /// transmission contract: it wires the `report_result` MCP server into
    /// the agent's launch, receives the payload over that data channel,
    /// and validates it against this schema. The schema is opaque to
    /// agentd: it enforces structure, not domain meaning.
    payload_schema: serde_json::Value,
}

/// MCP server name and tool name for the agentd-owned result channel.
/// agentd injects a stdio MCP server (a subcommand of this same binary)
/// into every contract session's launch; the agent calls `report_result`
/// with its payload, which is relayed byte-faithfully to agentd over the
/// existing unix socket (ADR 0035 R1).  tmux is never the result source.
const REPORT_RESULT_MCP_SERVER: &str = "doeff_result";
const REPORT_RESULT_TOOL: &str = "report_result";
/// argv subcommand that runs the stdio MCP server (see `run_report_result_mcp`).
const REPORT_RESULT_MCP_SUBCOMMAND: &str = "report-result-mcp";

/// The instruction agentd injects into the agent's first prompt telling
/// it how to return the structured result. This is agentd's transmission
/// contract with the terminal agent; the launcher never authors it, so a
/// launcher that knows only the data schema still gets a working result
/// channel.  ADR 0035: the result is recovered over a byte-faithful data
/// channel (the `report_result` MCP tool), never scraped from the screen.
fn result_protocol_instruction(_session_id: &str) -> String {
    format!(
        " Result channel: when you have finished the task, call the `{REPORT_RESULT_TOOL}` \
         MCP tool exactly once, passing your result as the `payload` argument — a JSON \
         object that satisfies the result schema. Do not print the result to the terminal \
         and do not create JSON result files; agentd only accepts the result through the \
         `{REPORT_RESULT_TOOL}` tool. If the tool responds with a validation error, fix the \
         payload and call `{REPORT_RESULT_TOOL}` again in the same session.",
    )
}

#[derive(Debug, Deserialize)]
struct LaunchParams {
    session_id: String,
    session_name: String,
    agent_type: String,
    work_dir: String,
    /// Optional explicit command override.  When provided, agentd sends this
    /// string verbatim to the new tmux pane and skips the agent-type-aware
    /// argv builder.  Required when agent_type is "generic" or unrecognized.
    #[serde(default)]
    command: Option<String>,
    #[serde(default)]
    prompt: Option<String>,
    #[serde(default)]
    model: Option<String>,
    #[serde(default)]
    effort: Option<String>,
    /// Map of MCP server name → URL passed to the agent.  Caller owns the
    /// MCP server lifecycle; agentd only forwards URLs into the agent argv.
    #[serde(default)]
    mcp_servers: BTreeMap<String, String>,
    /// When true, skip agent-specific pre-launch setup such as Codex's
    /// workspace trust file.  Use for sandboxed test invocations.
    #[serde(default)]
    skip_trust_setup: bool,
    #[serde(default = "default_session_lifecycle")]
    lifecycle: String,
    #[serde(default)]
    session_env: BTreeMap<String, String>,
    /// Optional structured-result contract. See 'ExpectedResultSpec' for
    /// the semantics; persisted with the session so the monitor can
    /// enforce it after the agent appears to finish.
    #[serde(default)]
    expected_result: Option<ExpectedResultSpec>,
}

#[derive(Debug, Deserialize)]
struct SessionIdParams {
    session_id: String,
}

/// Parameters for `session.report_result`: the agent's structured result
/// delivered over the agentd-owned data channel (ADR 0035). `payload` is
/// the exact JSON value the agent emitted; agentd persists it
/// byte-faithfully and never reconstructs it from the screen.
#[derive(Debug, Deserialize)]
struct ReportResultParams {
    session_id: String,
    payload: serde_json::Value,
}

#[derive(Debug, Deserialize)]
struct AwaitResultParams {
    session_id: String,
    /// Maximum number of seconds to block before returning a timeout
    /// error.  Defaults to `DEFAULT_AWAIT_TIMEOUT_SECONDS` (10 min) and
    /// is clamped into `[MIN_AWAIT_TIMEOUT_SECONDS, MAX_AWAIT_TIMEOUT_SECONDS]`
    /// inside the handler so misbehaving clients cannot park threads
    /// for arbitrarily long.
    #[serde(default)]
    timeout_seconds: Option<f64>,
}

#[derive(Debug, Deserialize)]
struct CaptureParams {
    session_id: String,
    #[serde(default = "default_capture_lines")]
    lines: i64,
}

#[derive(Debug, Deserialize)]
struct SendParams {
    session_id: String,
    message: String,
    #[serde(default = "default_true")]
    enter: bool,
    #[serde(default = "default_true")]
    literal: bool,
}

#[derive(Debug, Deserialize)]
struct ListParams {
    status: Option<Vec<String>>,
    agent_type: Option<String>,
    backend_kind: Option<String>,
    lifecycle: Option<String>,
}

fn default_capture_lines() -> i64 {
    100
}

fn default_true() -> bool {
    true
}

fn default_session_lifecycle() -> String {
    String::from(LIFECYCLE_RUN_TO_COMPLETION)
}

fn validate_session_lifecycle(lifecycle: &str) -> Result<()> {
    if lifecycle == LIFECYCLE_RUN_TO_COMPLETION || lifecycle == LIFECYCLE_INTERACTIVE {
        return Ok(());
    }
    Err(anyhow!(
        "unsupported session lifecycle: {} (expected {} or {})",
        lifecycle,
        LIFECYCLE_RUN_TO_COMPLETION,
        LIFECYCLE_INTERACTIVE
    ))
}

fn is_run_to_completion_lifecycle(lifecycle: &str) -> bool {
    lifecycle == LIFECYCLE_RUN_TO_COMPLETION
}

fn now_iso() -> String {
    Utc::now().to_rfc3339()
}

/// Open a sqlite connection with a non-zero busy timeout. Always use
/// this instead of `Connection::open` so the monitor thread does not
/// silently die the first time it races the serve thread for a write
/// lock.
fn open_conn(path: &Path) -> Result<Connection> {
    let conn = Connection::open(path)?;
    conn.busy_timeout(Duration::from_millis(u64::from(SQLITE_BUSY_TIMEOUT_MS)))?;
    Ok(conn)
}

/// Parse an ISO-8601 / RFC-3339 timestamp from the agent_sessions table
/// into UTC. Returns `None` for missing or malformed values; callers
/// treat that as "watchdog cannot evaluate this row" and fall through
/// to the regular tmux probe.
fn parse_iso_timestamp(raw: Option<&str>) -> Option<DateTime<Utc>> {
    raw.and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .map(|dt| dt.with_timezone(&Utc))
}

fn main() -> Result<()> {
    let raw: Vec<String> = env::args().skip(1).collect();
    // The `report-result-mcp` subcommand runs a stdio MCP server that
    // relays the agent's result to a running agentd (ADR 0035). It is a
    // pure socket client — no DB, lease, or serve loop.
    if raw.first().map(String::as_str) == Some(REPORT_RESULT_MCP_SUBCOMMAND) {
        return run_report_result_mcp(&raw[1..]);
    }
    let config = parse_args(raw)?;
    if let Some(parent) = config.db_path.parent() {
        fs::create_dir_all(parent)?;
    }
    if let Some(parent) = config.socket_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let conn = open_conn(&config.db_path)?;
    migrate(&conn)?;
    acquire_lease(&conn)?;
    // A fresh agentd has no way to verify what the previous instance
    // was waiting on — any 'awaiting_response' latches in the
    // session table refer to retry prompts the previous process sent
    // and that nobody is monitoring anymore.  Clear the latches so
    // the new monitor loop is free to re-evaluate turn-end on the
    // next stable observation instead of sitting on stale state
    // forever.
    conn.execute(
        "UPDATE agent_sessions SET awaiting_response = 0 \
         WHERE awaiting_response = 1 \
           AND status NOT IN ('done','failed','exited','stopped','cancelled')",
        [],
    )?;
    serve(config)
}

fn parse_args(args: Vec<String>) -> Result<Config> {
    let mut db_path: Option<PathBuf> = None;
    let mut socket_path: Option<PathBuf> = None;
    let mut tmux_bin = String::from("tmux");
    let mut monitor_interval = Duration::from_millis(DEFAULT_MONITOR_INTERVAL_MS);
    let mut max_running = DEFAULT_MAX_RUNNING_SESSIONS;
    let mut result_solicitation_limit =
        env_u32("DOEFF_AGENTD_RESULT_SOLICITATIONS").unwrap_or(DEFAULT_RESULT_SOLICITATION_LIMIT);
    let mut prompt_stall_seconds = env_positive_i64("DOEFF_AGENTD_PROMPT_STALL_SECS")
        .unwrap_or(DEFAULT_PROMPT_STALL_SECONDS);
    let mut prompt_unblock_limit =
        env_u32("DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS").unwrap_or(DEFAULT_PROMPT_UNBLOCK_LIMIT);
    let mut prompt_judge_cmd = normalize_prompt_judge_cmd(
        env::var("DOEFF_AGENTD_PROMPT_JUDGE_CMD")
            .unwrap_or_else(|_| String::from(DEFAULT_PROMPT_JUDGE_CMD)),
    );
    let mut command = String::from("serve");
    let mut index = 0;
    while index < args.len() {
        let arg = &args[index];
        if arg == "--db" {
            index += 1;
            db_path = args.get(index).map(PathBuf::from);
        } else if arg == "--socket" {
            index += 1;
            socket_path = args.get(index).map(PathBuf::from);
        } else if arg == "--tmux" {
            index += 1;
            tmux_bin = args
                .get(index)
                .cloned()
                .ok_or_else(|| anyhow!("--tmux requires a value"))?;
        } else if arg == "--monitor-interval-ms" {
            index += 1;
            let raw = args
                .get(index)
                .ok_or_else(|| anyhow!("--monitor-interval-ms requires a value"))?;
            monitor_interval = Duration::from_millis(raw.parse::<u64>()?);
        } else if arg == "--max-running" {
            index += 1;
            let raw = args
                .get(index)
                .ok_or_else(|| anyhow!("--max-running requires a value"))?;
            max_running = raw.parse::<usize>()?;
        } else if arg == "--result-solicitations" {
            index += 1;
            let raw = args
                .get(index)
                .ok_or_else(|| anyhow!("--result-solicitations requires a value"))?;
            result_solicitation_limit = raw.parse::<u32>()?;
        } else if arg == "--prompt-stall-secs" {
            index += 1;
            let raw = args
                .get(index)
                .ok_or_else(|| anyhow!("--prompt-stall-secs requires a value"))?;
            prompt_stall_seconds = raw.parse::<i64>()?;
            if prompt_stall_seconds <= 0 {
                return Err(anyhow!("--prompt-stall-secs must be positive"));
            }
        } else if arg == "--prompt-unblock-attempts" {
            index += 1;
            let raw = args
                .get(index)
                .ok_or_else(|| anyhow!("--prompt-unblock-attempts requires a value"))?;
            prompt_unblock_limit = raw.parse::<u32>()?;
        } else if arg == "--prompt-judge-cmd" {
            index += 1;
            let raw = args
                .get(index)
                .cloned()
                .ok_or_else(|| anyhow!("--prompt-judge-cmd requires a value"))?;
            prompt_judge_cmd = normalize_prompt_judge_cmd(raw);
        } else if arg == "serve" {
            command = arg.clone();
        } else {
            return Err(anyhow!("unknown argument: {}", arg));
        }
        index += 1;
    }
    if command != "serve" {
        return Err(anyhow!("unsupported command: {}", command));
    }
    Ok(Config {
        db_path: db_path.unwrap_or_else(default_db_path),
        socket_path: socket_path.unwrap_or_else(default_socket_path),
        tmux_bin,
        monitor_interval,
        max_running,
        result_solicitation_limit,
        prompt_stall_seconds,
        prompt_unblock_limit,
        prompt_judge_cmd,
    })
}

fn env_u32(name: &str) -> Option<u32> {
    env::var(name)
        .ok()
        .and_then(|s| s.trim().parse::<u32>().ok())
}

fn env_positive_i64(name: &str) -> Option<i64> {
    env::var(name)
        .ok()
        .and_then(|s| s.trim().parse::<i64>().ok())
        .filter(|&v| v > 0)
}

/// An empty (or blank) judge command means "judge disabled".
fn normalize_prompt_judge_cmd(raw: String) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn default_db_path() -> PathBuf {
    xdg_state_home().join("doeff").join("agentd.sqlite")
}

fn default_socket_path() -> PathBuf {
    if let Some(runtime_dir) = env::var_os("XDG_RUNTIME_DIR") {
        return PathBuf::from(runtime_dir).join("doeff").join("agentd.sock");
    }
    let user = env::var("USER")
        .or_else(|_| env::var("LOGNAME"))
        .unwrap_or_else(|_| String::from("unknown"));
    PathBuf::from("/tmp").join(format!("doeff-agentd-{user}.sock"))
}

fn xdg_state_home() -> PathBuf {
    env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".local").join("state"))
}

fn home_dir() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Absolute path to the running agentd binary, used as the command the
/// agent spawns for the `report_result` stdio MCP server.  Falls back to
/// the bare binary name (PATH lookup) if the exe path cannot be resolved.
fn agentd_binary_path() -> String {
    env::current_exe()
        .ok()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|| String::from("doeff-agentd"))
}

/// Run the stdio MCP server that delivers an agent's result to agentd
/// (ADR 0035 R1).  agentd wires `<agentd-bin> report-result-mcp --session
/// <id> --socket <path>` into every contract session's MCP config; the
/// agent calls the `report_result` tool, and this server relays the
/// payload verbatim to agentd's `session.report_result` RPC over the unix
/// socket.  The result travels as JSON end to end — tmux is never a source,
/// so the transport is byte-faithful (no fixed-width grid projection).
fn run_report_result_mcp(args: &[String]) -> Result<()> {
    let mut session_id: Option<String> = None;
    let mut socket: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--session" => {
                i += 1;
                session_id = args.get(i).cloned();
            }
            "--socket" => {
                i += 1;
                socket = args.get(i).cloned();
            }
            other => return Err(anyhow!("report-result-mcp: unknown argument: {other}")),
        }
        i += 1;
    }
    let session_id =
        session_id.ok_or_else(|| anyhow!("report-result-mcp requires --session <id>"))?;
    let socket = socket.ok_or_else(|| anyhow!("report-result-mcp requires --socket <path>"))?;

    let stdin = std::io::stdin();
    let mut reader = stdin.lock();
    let mut stdout = std::io::stdout();
    let mut line = String::new();
    loop {
        line.clear();
        let read = reader.read_line(&mut line)?;
        if read == 0 {
            break;
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            // A malformed line is ignored rather than crashing the server
            // (a crash would kill the agent's only result channel).
            Err(_) => continue,
        };
        if let Some(response) = handle_mcp_message(&msg, &session_id, &socket) {
            let encoded = serde_json::to_string(&response)?;
            stdout.write_all(encoded.as_bytes())?;
            stdout.write_all(b"\n")?;
            stdout.flush()?;
        }
    }
    Ok(())
}

/// Dispatch one MCP JSON-RPC message. Returns `Some(response)` for
/// requests and `None` for notifications (no `id`).
fn handle_mcp_message(msg: &Value, session_id: &str, socket: &str) -> Option<Value> {
    let method = msg.get("method").and_then(Value::as_str).unwrap_or("");
    let id = msg.get("id").cloned();
    match method {
        "initialize" => {
            let protocol = msg
                .get("params")
                .and_then(|p| p.get("protocolVersion"))
                .and_then(Value::as_str)
                .unwrap_or(MCP_PROTOCOL_VERSION);
            Some(mcp_result(
                id,
                json!({
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "doeff-agentd-report-result",
                        "version": env!("CARGO_PKG_VERSION"),
                    },
                }),
            ))
        }
        "notifications/initialized" => None,
        "ping" => Some(mcp_result(id, json!({}))),
        "tools/list" => Some(mcp_result(id, json!({ "tools": [report_result_tool_def()] }))),
        "tools/call" => Some(handle_report_result_tool_call(
            id,
            msg.get("params"),
            session_id,
            socket,
        )),
        _ => {
            if id.is_none() {
                None
            } else {
                Some(mcp_error(id, -32601, format!("method not found: {method}")))
            }
        }
    }
}

fn report_result_tool_def() -> Value {
    json!({
        "name": REPORT_RESULT_TOOL,
        "description": "Report this session's final structured result to agentd. Call exactly \
                        once with your result as the `payload` argument. agentd validates it \
                        against the session's result schema and records it byte-faithfully; if \
                        it responds with a validation error, fix the payload and call again.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "description": "The result object satisfying the session's result schema."
                }
            },
            "required": ["payload"]
        }
    })
}

fn handle_report_result_tool_call(
    id: Option<Value>,
    params: Option<&Value>,
    session_id: &str,
    socket: &str,
) -> Value {
    let name = params
        .and_then(|p| p.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if name != REPORT_RESULT_TOOL {
        return mcp_tool_error(id, format!("unknown tool: {name}"));
    }
    let payload = match params
        .and_then(|p| p.get("arguments"))
        .and_then(|a| a.get("payload"))
    {
        Some(p) => p.clone(),
        None => {
            return mcp_tool_error(
                id,
                format!("{REPORT_RESULT_TOOL} requires a `payload` argument"),
            )
        }
    };
    match relay_report_result(socket, session_id, &payload) {
        Ok(_) => mcp_result(
            id,
            json!({
                "content": [{"type": "text", "text": "result recorded"}],
                "isError": false,
            }),
        ),
        // A rejected/invalid result is surfaced as an MCP tool error (not a
        // transport error) so the agent sees the reason and can correct the
        // payload within the same turn — the rejection itself is final for
        // that payload (no re-validation, ADR 0035 R4); a missing result at
        // turn-end is handled by the monitor's bounded solicitation loop
        // (ADR-DOE-AGENTS-002 R1/R3).
        Err(e) => mcp_tool_error(id, format!("{e:#}")),
    }
}

/// Relay a `report_result` payload to a running agentd over its unix
/// socket via the `session.report_result` RPC.
fn relay_report_result(socket: &str, session_id: &str, payload: &Value) -> Result<Value> {
    let stream = UnixStream::connect(socket)
        .with_context(|| format!("connecting to agentd socket {socket}"))?;
    let mut writer = stream.try_clone()?;
    let mut reader = BufReader::new(stream);
    let request = json!({
        "id": 1,
        "method": "session.report_result",
        "params": {"session_id": session_id, "payload": payload},
    });
    let mut encoded = serde_json::to_string(&request)?;
    encoded.push('\n');
    writer.write_all(encoded.as_bytes())?;
    writer.flush()?;
    let mut response_line = String::new();
    reader.read_line(&mut response_line)?;
    let response: RpcResponse = serde_json::from_str(response_line.trim())
        .with_context(|| "parsing agentd session.report_result response")?;
    if response.ok {
        Ok(response.result.unwrap_or(Value::Null))
    } else {
        Err(anyhow!(
            "{}",
            response
                .error
                .unwrap_or_else(|| String::from("session.report_result failed"))
        ))
    }
}

fn mcp_result(id: Option<Value>, result: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id.unwrap_or(Value::Null), "result": result})
}

fn mcp_error(id: Option<Value>, code: i32, message: String) -> Value {
    json!({"jsonrpc": "2.0", "id": id.unwrap_or(Value::Null), "error": {"code": code, "message": message}})
}

/// A tool-level error: a well-formed `tools/call` response whose content
/// carries the failure and sets `isError`, so the agent (not the MCP
/// transport) handles it.
fn mcp_tool_error(id: Option<Value>, text: String) -> Value {
    mcp_result(
        id,
        json!({
            "content": [{"type": "text", "text": format!("Error: {text}")}],
            "isError": true,
        }),
    )
}

fn migrate(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS agent_sessions (
          session_id TEXT PRIMARY KEY,
          session_name TEXT NOT NULL,
          pane_id TEXT NOT NULL,
          agent_type TEXT NOT NULL,
          work_dir TEXT NOT NULL,
          status TEXT NOT NULL,
          backend_kind TEXT NOT NULL,
          backend_ref_json TEXT NOT NULL,
          started_at TEXT NOT NULL,
          last_observed_at TEXT,
          finished_at TEXT,
          cleaned_at TEXT,
          pr_url TEXT,
          output_snippet TEXT,
          terminal_cause_json TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_session_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_session_commands (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT,
          command_type TEXT NOT NULL,
          requested_at TEXT NOT NULL,
          completed_at TEXT,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          error TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_daemon_lease (
          lease_name TEXT PRIMARY KEY,
          owner_pid INTEGER NOT NULL,
          heartbeat_at TEXT NOT NULL,
          expires_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_agent_sessions_status
          ON agent_sessions(status);
        CREATE INDEX IF NOT EXISTS idx_agent_session_events_session
          ON agent_session_events(session_id, id);
        "#,
    )?;
    ensure_column(
        conn,
        "agent_sessions",
        "lifecycle",
        "TEXT NOT NULL DEFAULT 'run_to_completion'",
    )?;
    ensure_column(conn, "agent_sessions", "expected_result_json", "TEXT")?;
    ensure_column(
        conn,
        "agent_sessions",
        "retries_used",
        "INTEGER NOT NULL DEFAULT 0",
    )?;
    ensure_column(conn, "agent_sessions", "last_validation_error", "TEXT")?;
    ensure_column(
        conn,
        "agent_sessions",
        "awaiting_response",
        "INTEGER NOT NULL DEFAULT 0",
    )?;
    ensure_column(conn, "agent_sessions", "observed_active_at", "TEXT")?;
    ensure_column(conn, "agent_sessions", "terminal_cause_json", "TEXT")?;
    ensure_column(conn, "agent_sessions", "result_payload_json", "TEXT")?;
    ensure_column(
        conn,
        "agent_sessions",
        "result_solicitations_used",
        "INTEGER NOT NULL DEFAULT 0",
    )?;
    ensure_column(
        conn,
        "agent_sessions",
        "prompt_unblock_attempts",
        "INTEGER NOT NULL DEFAULT 0",
    )?;
    ensure_column(conn, "agent_sessions", "last_output_change_at", "TEXT")?;
    Ok(())
}

fn ensure_column(conn: &Connection, table: &str, column: &str, definition: &str) -> Result<()> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = stmt.query_map([], |row| row.get::<_, String>(1))?;
    for row in rows {
        if row? == column {
            return Ok(());
        }
    }
    conn.execute(
        &format!("ALTER TABLE {table} ADD COLUMN {column} {definition}"),
        [],
    )?;
    Ok(())
}

fn prepare_socket_path(socket_path: &PathBuf) -> Result<()> {
    if !socket_path.exists() {
        return Ok(());
    }
    if UnixStream::connect(socket_path).is_ok() {
        return Err(anyhow!(
            "doeff-agentd is already listening on {}",
            socket_path.display()
        ));
    }
    fs::remove_file(socket_path)
        .with_context(|| format!("failed to remove stale socket {}", socket_path.display()))?;
    Ok(())
}

fn acquire_lease(conn: &Connection) -> Result<()> {
    conn.execute_batch("BEGIN IMMEDIATE")?;
    let result = acquire_lease_in_transaction(conn);
    if result.is_ok() {
        conn.execute_batch("COMMIT")?;
    } else {
        let _ = conn.execute_batch("ROLLBACK");
    }
    result
}

fn acquire_lease_in_transaction(conn: &Connection) -> Result<()> {
    let existing = read_lease(conn)?;
    if let Some(lease) = existing {
        let expires_at = parse_datetime(&lease.expires_at)?;
        if expires_at > Utc::now() {
            return Err(anyhow!(
                "doeff-agentd lease is active: owner_pid={} expires_at={}",
                lease.owner_pid,
                lease.expires_at
            ));
        }
    }
    upsert_lease(conn)
}

fn read_lease(conn: &Connection) -> Result<Option<LeaseSnapshot>> {
    conn.query_row(
        "SELECT lease_name, owner_pid, heartbeat_at, expires_at
         FROM agent_daemon_lease WHERE lease_name = ?1",
        params![LEASE_NAME],
        |row| {
            Ok(LeaseSnapshot {
                lease_name: row.get(0)?,
                owner_pid: row.get(1)?,
                heartbeat_at: row.get(2)?,
                expires_at: row.get(3)?,
            })
        },
    )
    .optional()
    .map_err(Into::into)
}

fn upsert_lease(conn: &Connection) -> Result<()> {
    let now = Utc::now();
    let expires_at = now + ChronoDuration::seconds(LEASE_TTL_SECONDS);
    conn.execute(
        "INSERT INTO agent_daemon_lease
          (lease_name, owner_pid, heartbeat_at, expires_at)
         VALUES (?1, ?2, ?3, ?4)
         ON CONFLICT(lease_name) DO UPDATE SET
          owner_pid = excluded.owner_pid,
          heartbeat_at = excluded.heartbeat_at,
          expires_at = excluded.expires_at",
        params![
            LEASE_NAME,
            i64::from(std::process::id()),
            now.to_rfc3339(),
            expires_at.to_rfc3339(),
        ],
    )?;
    Ok(())
}

fn serve(config: Config) -> Result<()> {
    prepare_socket_path(&config.socket_path)?;
    let listener = UnixListener::bind(&config.socket_path)?;
    let monitor_config = config.clone();
    thread::spawn(move || monitor_loop(monitor_config));
    let heartbeat_config = config.clone();
    thread::spawn(move || heartbeat_loop(heartbeat_config));
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                let worker_config = config.clone();
                thread::spawn(move || {
                    if let Err(err) = handle_stream(stream, worker_config) {
                        eprintln!("doeff-agentd client error: {err:#}");
                    }
                });
            }
            Err(err) => eprintln!("doeff-agentd accept error: {err}"),
        }
    }
    Ok(())
}

fn handle_stream(stream: UnixStream, config: Config) -> Result<()> {
    let reader_stream = stream.try_clone()?;
    let mut reader = BufReader::new(reader_stream);
    let mut writer = stream;
    let conn = open_conn(&config.db_path)?;
    migrate(&conn)?;
    loop {
        let mut line = String::new();
        let read = reader.read_line(&mut line)?;
        if read == 0 {
            break;
        }
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<RpcRequest>(&line) {
            Ok(request) => dispatch_request(&conn, &config, request),
            Err(err) => RpcResponse {
                id: Value::Null,
                ok: false,
                result: None,
                error: Some(format!("invalid request: {err}")),
                error_code: None,
            },
        };
        let encoded = serde_json::to_string(&response)?;
        writer.write_all(encoded.as_bytes())?;
        writer.write_all(b"\n")?;
        writer.flush()?;
    }
    Ok(())
}

fn dispatch_request(conn: &Connection, config: &Config, request: RpcRequest) -> RpcResponse {
    let id = request.id.clone();
    let result = dispatch_request_result(conn, config, request);
    match result {
        Ok(value) => RpcResponse {
            id,
            ok: true,
            result: Some(value),
            error: None,
            error_code: None,
        },
        Err(err) => {
            // If the handler raised a structured RpcError, surface its
            // code on the wire so callers (notably session.await_result
            // clients) can distinguish failure modes without parsing
            // the message.  Fall back to the legacy plain-string error
            // for handlers that haven't migrated.
            let (message, code) = match err.downcast_ref::<RpcError>() {
                Some(rpc_err) => (rpc_err.message.clone(), Some(rpc_err.code)),
                None => (format!("{err:#}"), None),
            };
            RpcResponse {
                id,
                ok: false,
                result: None,
                error: Some(message),
                error_code: code,
            }
        }
    }
}

fn dispatch_request_result(
    conn: &Connection,
    config: &Config,
    request: RpcRequest,
) -> Result<Value> {
    if request.method == "daemon.status" {
        let active_count = count_active_sessions(conn)?;
        let lease = read_lease(conn)?;
        Ok(json!({
            "state": "running",
            "pid": std::process::id(),
            "db_path": config.db_path,
            "socket_path": config.socket_path,
            "max_running": config.max_running,
            "active_sessions": active_count,
            "lease": lease,
        }))
    } else if request.method == "session.launch" {
        let params: LaunchParams = serde_json::from_value(request.params)?;
        let snapshot = session_launch(conn, config, params)?;
        Ok(serde_json::to_value(snapshot)?)
    } else if request.method == "session.get" {
        let params: SessionIdParams = serde_json::from_value(request.params)?;
        let snapshot = session_get(conn, &params.session_id)?;
        Ok(serde_json::to_value(snapshot)?)
    } else if request.method == "session.list" {
        let params: ListParams = serde_json::from_value(request.params)?;
        let snapshots = session_list(conn, params)?;
        Ok(serde_json::to_value(snapshots)?)
    } else if request.method == "session.capture" {
        let params: CaptureParams = serde_json::from_value(request.params)?;
        let text = session_capture(conn, config, params)?;
        Ok(json!({"text": text}))
    } else if request.method == "session.send" {
        let params: SendParams = serde_json::from_value(request.params)?;
        session_send(conn, config, params)?;
        Ok(json!({"sent": true}))
    } else if request.method == "session.cancel" {
        let params: SessionIdParams = serde_json::from_value(request.params)?;
        let snapshot = session_cancel(conn, config, &params.session_id)?;
        Ok(serde_json::to_value(snapshot)?)
    } else if request.method == "session.cleanup" {
        let params: SessionIdParams = serde_json::from_value(request.params)?;
        let snapshot = session_cleanup(conn, config, &params.session_id)?;
        Ok(serde_json::to_value(snapshot)?)
    } else if request.method == "session.await_result" {
        let params: AwaitResultParams = serde_json::from_value(request.params)?;
        session_await_result(conn, params)
    } else if request.method == "session.report_result" {
        let params: ReportResultParams = serde_json::from_value(request.params)?;
        session_report_result(conn, params)
    } else {
        Err(anyhow!("unknown method: {}", request.method))
    }
}

/// The agentd-owned result channel wired into a contract session's launch.
/// agentd runs a stdio MCP server (a `report-result-mcp` subcommand of its
/// own binary) that the agent spawns; the server relays `report_result`
/// calls to agentd over `socket`.  This is how ADR 0035's byte-faithful,
/// agentd-owned transport is delivered without scraping the screen.
#[derive(Debug, Clone)]
struct ResultChannel {
    /// Absolute path to the agentd binary (the stdio MCP server command).
    command: String,
    session_id: String,
    /// Path to agentd's unix socket the stdio server relays results to.
    socket: String,
}

impl ResultChannel {
    /// argv the agent uses to spawn the stdio MCP server.
    fn mcp_command_args(&self) -> Vec<String> {
        vec![
            String::from(REPORT_RESULT_MCP_SUBCOMMAND),
            String::from("--session"),
            self.session_id.clone(),
            String::from("--socket"),
            self.socket.clone(),
        ]
    }
}

/// Build the shell command line that tmux runs in the new pane.  Per-agent
/// adapters (codex, claude) own the argv shape; callers passing
/// `agent_type=generic` (or any unknown type) must provide `command`
/// explicitly as an escape hatch.  When `result_channel` is set the agent
/// gets the agentd-owned `report_result` MCP server wired into its argv.
fn resolve_launch_command(
    params: &LaunchParams,
    result_channel: Option<&ResultChannel>,
) -> Result<String> {
    if let Some(explicit) = params.command.as_ref() {
        if !explicit.trim().is_empty() {
            return Ok(explicit.clone());
        }
    }
    match params.agent_type.as_str() {
        "codex" => Ok(shell_join(build_codex_argv(params, result_channel))),
        "claude" => Ok(shell_join(build_claude_argv(params, result_channel))),
        "generic" | "" => Err(anyhow!(
            "session.launch: agent_type='{}' requires an explicit `command`",
            params.agent_type
        )),
        other => Err(anyhow!(
            "session.launch: unknown agent_type '{}'; pass `command` to use generic launch",
            other
        )),
    }
}

fn build_codex_argv(params: &LaunchParams, result_channel: Option<&ResultChannel>) -> Vec<String> {
    let mut args: Vec<String> = vec![String::from("codex"), String::from("--yolo")];
    if let Some(effort) = params.effort.as_ref() {
        if !effort.is_empty() {
            args.push(String::from("-c"));
            args.push(format!(
                "model_reasoning_effort={}",
                toml_quoted_string(effort)
            ));
        }
    }
    for (name, url) in &params.mcp_servers {
        args.push(String::from("-c"));
        args.push(format!(
            "mcp_servers.{}.url={}",
            toml_quoted_key(name),
            toml_quoted_string(url)
        ));
    }
    // Wire the agentd-owned result channel as a stdio MCP server. codex
    // takes `mcp_servers.<name>.command` + `.args` config overrides via
    // `-c`; the args are a TOML array of quoted strings.
    if let Some(channel) = result_channel {
        args.push(String::from("-c"));
        args.push(format!(
            "mcp_servers.{}.command={}",
            toml_quoted_key(REPORT_RESULT_MCP_SERVER),
            toml_quoted_string(&channel.command)
        ));
        let arg_items = channel
            .mcp_command_args()
            .into_iter()
            .map(|a| toml_quoted_string(&a))
            .collect::<Vec<_>>()
            .join(",");
        args.push(String::from("-c"));
        args.push(format!(
            "mcp_servers.{}.args=[{}]",
            toml_quoted_key(REPORT_RESULT_MCP_SERVER),
            arg_items
        ));
    }
    if let Some(model) = params.model.as_ref() {
        if !model.is_empty() {
            args.push(String::from("--model"));
            args.push(model.clone());
        }
    }
    // Intentionally do NOT pass the prompt as a positional argument.
    // Codex would treat that as a single-shot invocation and exit when
    // the task completed, which destroys the agent process before the
    // monitor can validate output or send follow-up feedback.
    //
    // Instead we leave codex in its interactive REPL and 'session_launch'
    // sends the prompt as the first message into the live session via
    // 'tmux_send_keys', keeping codex alive for retries with full
    // conversation context.
    args
}

fn build_claude_argv(params: &LaunchParams, result_channel: Option<&ResultChannel>) -> Vec<String> {
    let mut args: Vec<String> = vec![
        String::from("claude"),
        String::from("--dangerously-skip-permissions"),
        // Unattended sessions must not inherit the config-dir owner's
        // interactive workflow hooks: measured 2026-07-05, a Stop-hook
        // chain truncated an agent turn mid-work and swallowed the
        // queued result solicitation (and the prompt judge died with
        // "Operation stopped by hook").  Hooks are human-workflow
        // config; the agent contract is the result channel below.
        String::from("--settings"),
        String::from("{\"disableAllHooks\":true}"),
    ];
    // Effort delivery, symmetric with build_codex_argv's
    // model_reasoning_effort: the claude CLI has a real --effort flag.
    if let Some(effort) = params.effort.as_ref() {
        if !effort.is_empty() {
            args.push(String::from("--effort"));
            args.push(effort.clone());
        }
    }
    if let Some(model) = params.model.as_ref() {
        if !model.is_empty() {
            args.push(String::from("--model"));
            args.push(model.clone());
        }
    }
    // MCP wiring. The Rust launcher previously left this unimplemented
    // (only the Python adapter wired it), so a claude launch could never
    // deliver a result and would be falsely rejected by the ADR 0035
    // reject-at-launch gate. Wire both caller-supplied servers (SSE, to
    // match doeff-agents/adapters/claude.py) and the agentd-owned
    // `report_result` stdio server into a single --mcp-config.
    let mut mcp_servers = serde_json::Map::new();
    for (name, url) in &params.mcp_servers {
        mcp_servers.insert(
            name.clone(),
            serde_json::json!({"type": "sse", "url": url}),
        );
    }
    if let Some(channel) = result_channel {
        mcp_servers.insert(
            String::from(REPORT_RESULT_MCP_SERVER),
            serde_json::json!({
                "type": "stdio",
                "command": channel.command,
                "args": channel.mcp_command_args(),
            }),
        );
    }
    if !mcp_servers.is_empty() {
        let mcp_config = serde_json::json!({ "mcpServers": mcp_servers });
        args.push(String::from("--mcp-config"));
        args.push(serde_json::to_string(&mcp_config).unwrap_or_else(|_| String::from("{}")));
        args.push(String::from("--strict-mcp-config"));
    }
    // Same rationale as 'build_codex_argv': the prompt is sent as a
    // message into the running agent (not as a positional argv) so the
    // session stays alive past task completion.
    args
}

fn run_pre_launch_setup(params: &LaunchParams) -> Result<()> {
    if params.agent_type == "codex" {
        if let Err(err) = trust_codex_workspace(&params.work_dir) {
            eprintln!(
                "doeff-agentd: warning: failed to persist Codex workspace trust for {}: {err:#}",
                params.work_dir
            );
        }
    }
    if params.agent_type == "claude" {
        if let Err(err) = trust_claude_workspace(&params.work_dir, &params.session_env) {
            eprintln!(
                "doeff-agentd: warning: failed to persist Claude workspace trust for {}: {err:#}",
                params.work_dir
            );
        }
    }
    Ok(())
}

/// Persist Claude Code's per-workspace trust in
/// `<CLAUDE_CONFIG_DIR>/.claude.json` so a fresh workspace does not stall the
/// launch on the interactive "do you trust this folder?" dialog — the claude
/// twin of `trust_codex_workspace` (ACP ADR 0043: claude agent runtime).
/// Claude keys projects by the REALPATH of the cwd (`/tmp` shows up as
/// `/private/tmp` on macOS), so the work dir is canonicalised first.
fn trust_claude_workspace(
    work_dir: &str,
    session_env: &std::collections::BTreeMap<String, String>,
) -> Result<()> {
    let config_dir = session_env
        .get("CLAUDE_CONFIG_DIR")
        .map(PathBuf::from)
        .or_else(|| env::var_os("CLAUDE_CONFIG_DIR").map(PathBuf::from))
        .unwrap_or_else(|| home_dir().join(".claude"));
    fs::create_dir_all(&config_dir)
        .with_context(|| format!("creating claude config dir: {}", config_dir.display()))?;
    let trusted_dir = fs::canonicalize(work_dir)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| work_dir.to_string());
    let state_path = config_dir.join(".claude.json");
    let mut state: serde_json::Value = if state_path.exists() {
        let raw = fs::read_to_string(&state_path)
            .with_context(|| format!("reading claude state: {}", state_path.display()))?;
        serde_json::from_str(&raw)
            .with_context(|| format!("parsing claude state: {}", state_path.display()))?
    } else {
        serde_json::json!({})
    };
    let root = state
        .as_object_mut()
        .context("claude state file is not a JSON object")?;
    let projects = root
        .entry("projects")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .context("claude state 'projects' is not a JSON object")?;
    let project = projects
        .entry(trusted_dir)
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .context("claude state project entry is not a JSON object")?;
    project.insert(
        "hasTrustDialogAccepted".to_string(),
        serde_json::json!(true),
    );
    project.insert(
        "hasCompletedProjectOnboarding".to_string(),
        serde_json::json!(true),
    );
    let serialized = serde_json::to_string(&state)?;
    // write-new + rename so a concurrently-running claude session never
    // reads a torn state file.
    let tmp_path = config_dir.join(".claude.json.agentd-tmp");
    fs::write(&tmp_path, serialized)
        .with_context(|| format!("writing claude state: {}", tmp_path.display()))?;
    fs::rename(&tmp_path, &state_path)
        .with_context(|| format!("installing claude state: {}", state_path.display()))?;
    Ok(())
}

/// Persist Codex's per-workspace trust in `~/.codex/config.toml` so launching
/// without `--yolo` (or after a Codex update that drops `--yolo`) still skips
/// the "Do you trust this directory?" prompt.  Mirrors the helper in
/// doeff-agents/adapters/codex.py.
fn trust_codex_workspace(work_dir: &str) -> Result<()> {
    let codex_home = env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".codex"));
    fs::create_dir_all(&codex_home)
        .with_context(|| format!("creating codex home: {}", codex_home.display()))?;
    let config_path = codex_home.join("config.toml");
    let existing = if config_path.exists() {
        fs::read_to_string(&config_path)
            .with_context(|| format!("reading codex config: {}", config_path.display()))?
    } else {
        String::new()
    };
    let header = format!("[projects.{}]", toml_quoted_key(work_dir));
    let trust_line = String::from("trust_level = \"trusted\"");
    let mut lines: Vec<String> = existing.lines().map(|s| s.to_string()).collect();
    let mut replaced = false;
    let mut header_index: Option<usize> = None;
    for (index, line) in lines.iter().enumerate() {
        if line.trim() == header {
            header_index = Some(index);
            break;
        }
    }
    if let Some(start) = header_index {
        let mut end = start + 1;
        while end < lines.len() && !lines[end].starts_with('[') {
            end += 1;
        }
        for line in lines.iter_mut().take(end).skip(start + 1) {
            if line.trim_start().starts_with("trust_level") {
                *line = trust_line.clone();
                replaced = true;
                break;
            }
        }
        if !replaced {
            lines.insert(start + 1, trust_line.clone());
            replaced = true;
        }
    } else {
        if !lines.is_empty() && !lines.last().map(|s| s.is_empty()).unwrap_or(true) {
            lines.push(String::new());
        }
        lines.push(header);
        lines.push(trust_line);
        replaced = true;
    }
    if replaced {
        let mut output = lines.join("\n");
        if !output.ends_with('\n') {
            output.push('\n');
        }
        fs::write(&config_path, output)
            .with_context(|| format!("writing codex config: {}", config_path.display()))?;
    }
    Ok(())
}

fn toml_quoted_key(value: &str) -> String {
    let escaped = value.replace('\\', "\\\\").replace('"', "\\\"");
    format!("\"{escaped}\"")
}

fn toml_quoted_string(value: &str) -> String {
    let escaped = value.replace('\\', "\\\\").replace('"', "\\\"");
    format!("\"{escaped}\"")
}

fn shell_join(args: Vec<String>) -> String {
    args.into_iter()
        .map(shell_quote)
        .collect::<Vec<_>>()
        .join(" ")
}

fn shell_quote(value: String) -> String {
    if value.is_empty() {
        return String::from("''");
    }
    let safe = value
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "-_./:=@,%+".contains(c));
    if safe {
        value
    } else {
        let escaped = value.replace('\'', "'\\''");
        format!("'{escaped}'")
    }
}

/// True when a caller-supplied command line will run codex: any
/// whitespace token equal to `codex` or ending in `/codex`.  Substrings
/// inside other words (e.g. `codexify`) do not count.
fn command_mentions_codex(command: &str) -> bool {
    command
        .split_whitespace()
        .any(|token| token == "codex" || token.ends_with("/codex"))
}

fn session_launch(
    conn: &Connection,
    config: &Config,
    params: LaunchParams,
) -> Result<SessionSnapshot> {
    session_launch_with_ready_timeout(conn, config, params, Duration::from_secs(120))
}

fn session_launch_with_ready_timeout(
    conn: &Connection,
    config: &Config,
    params: LaunchParams,
    ready_timeout: Duration,
) -> Result<SessionSnapshot> {
    validate_session_lifecycle(&params.lifecycle)?;
    if session_get(conn, &params.session_id)?.is_some() {
        return Err(anyhow!(
            "session is already registered: {}",
            params.session_id
        ));
    }
    let active_count = count_active_sessions(conn)?;
    if active_count >= config.max_running {
        return Err(anyhow!(
            "max running agent sessions reached: {active_count}/{}",
            config.max_running
        ));
    }
    if tmux_has_session(config, &params.session_name)? {
        return Err(anyhow!(
            "tmux session already exists: {}",
            params.session_name
        ));
    }
    // ADR-DOE-AGENTS-003: an agent's auth profile is a per-project
    // (per-namespace) decision with NO default.  A codex launch without an
    // explicit CODEX_HOME silently inherits whatever account lives in
    // ~/.codex — on shared machines the PERSONAL one (observed live
    // 2026-07-04: ACP's catalog registered bare `codex` commands and an
    // unattended session burned the personal weekly quota).  Reject at
    // launch instead of guessing.
    let launches_codex = params.agent_type == "codex"
        || params
            .command
            .as_deref()
            .map(command_mentions_codex)
            .unwrap_or(false);
    if launches_codex {
        let has_explicit_codex_home = params.session_env.contains_key("CODEX_HOME")
            || params
                .command
                .as_deref()
                .map(|c| c.contains("CODEX_HOME="))
                .unwrap_or(false);
        if !has_explicit_codex_home {
            return Err(anyhow!(
                "session.launch: no agent auth profile for a codex session — set CODEX_HOME \
                 explicitly (session_env or the launch command). There is NO default: the \
                 implicit ~/.codex fallback selects whatever account lives there. Declare \
                 the auth profile per project/namespace (ADR-DOE-AGENTS-003)."
            ));
        }
    }
    // Staged enforcement (ADR-DOE-AGENTS-003 R3): claude sessions only warn
    // until existing callers declare CLAUDE_CONFIG_DIR explicitly.
    if params.agent_type == "claude" && !params.session_env.contains_key("CLAUDE_CONFIG_DIR") {
        eprintln!(
            "doeff-agentd WARNING: claude session {} launched without an explicit \
             CLAUDE_CONFIG_DIR auth profile (ADR-DOE-AGENTS-003 R3: enforcement \
             follows once callers migrate)",
            params.session_id
        );
    }
    // ADR 0035: a contract session (expected_result set) delivers its
    // result over the agentd-owned `report_result` MCP channel. Wire it
    // for codex/claude launches agentd builds the argv for; a caller that
    // supplied an explicit `command` owns its own reporting (escape hatch).
    let has_command_override = params
        .command
        .as_ref()
        .map(|c| !c.trim().is_empty())
        .unwrap_or(false);
    let result_channel = if params.expected_result.is_some()
        && !has_command_override
        && matches!(params.agent_type.as_str(), "codex" | "claude")
    {
        Some(ResultChannel {
            command: agentd_binary_path(),
            session_id: params.session_id.clone(),
            socket: config.socket_path.to_string_lossy().into_owned(),
        })
    } else {
        None
    };
    // Reject-at-launch gate (ADR 0035, fork #13): an agent that agentd
    // cannot wire the result channel into can never deliver a result —
    // reject up front instead of silently accepting a session that will
    // only ever time out without a result.
    if params.expected_result.is_some() && result_channel.is_none() && !has_command_override {
        return Err(anyhow!(
            "session.launch: agent_type '{}' cannot deliver a result over the \
             {REPORT_RESULT_TOOL} channel; a result contract requires agent_type \
             'codex' or 'claude' (or an explicit `command` that reports results itself)",
            params.agent_type
        ));
    }
    let command_line = resolve_launch_command(&params, result_channel.as_ref())?;
    if !params.skip_trust_setup {
        run_pre_launch_setup(&params)?;
    }
    let pane_id = tmux_new_session(
        config,
        &params.session_name,
        &params.work_dir,
        &params.session_env,
    )?;
    // Physical session bookkeeping is independent of TUI readiness. Publish
    // BOOTING immediately so external reconcilers never mistake the normal
    // ready wait (up to 120s) for an orphaned tmux session.
    let mut backend_ref = BTreeMap::new();
    backend_ref.insert(String::from("session_name"), params.session_name.clone());
    backend_ref.insert(String::from("pane_id"), pane_id.clone());
    backend_ref.insert(String::from("command"), command_line.clone());
    let mut snapshot = SessionSnapshot {
        session_id: params.session_id.clone(),
        session_name: params.session_name.clone(),
        pane_id: pane_id.clone(),
        agent_type: params.agent_type.clone(),
        work_dir: params.work_dir.clone(),
        lifecycle: params.lifecycle.clone(),
        status: String::from("booting"),
        backend_kind: String::from("tmux"),
        backend_ref,
        started_at: now_iso(),
        last_observed_at: None,
        finished_at: None,
        cleaned_at: None,
        pr_url: None,
        output_snippet: None,
        terminal_cause: None,
        expected_result: params.expected_result.clone(),
        retries_used: 0,
        last_validation_error: None,
        awaiting_response: false,
        observed_active_at: None,
        result_payload: None,
        result_solicitations_used: 0,
        prompt_unblock_attempts: 0,
        last_output_change_at: None,
    };
    upsert_snapshot(conn, &snapshot)?;
    record_event(conn, &snapshot.session_id, "session_started", &snapshot)?;

    if !command_line.trim().is_empty() {
        tmux_send_keys(config, &pane_id, &command_line, true, true)?;
    }
    // The prompt is sent as a message INTO the running agent's REPL — not
    // as a positional argv or print-mode stdin — so the session survives
    // task completion and the monitor can re-prompt the still-alive agent
    // when the output contract is violated.
    let mut awaiting_response = false;
    if let Some(prompt) = params.prompt.as_ref() {
        if !prompt.trim().is_empty() {
            // agentd owns the result transmission contract: when an
            // `expected_result` is attached, append the HOW/WHERE
            // instruction here so the launcher never has to author
            // file/path/envelope prose.  The launcher's prompt
            // describes WHAT data to report; agentd adds where to
            // put it and how it is validated.
            let full_prompt = if params.expected_result.is_some() {
                format!(
                    "{prompt}{}",
                    result_protocol_instruction(&params.session_id)
                )
            } else {
                prompt.clone()
            };
            // Wait for the agent's REPL to actually be ready for
            // input before sending the prompt + Enter.  Codex (and
            // similar) print their banner, load MCP servers, and
            // only then enter the input loop.  Sending keys before
            // that race lets the text queue up while the Enter is
            // eaten by the loading screen — the visible symptom
            // was a prompt sitting in codex's input box that was
            // never submitted.
            if params.command.is_none() && is_interactive_agent_type(&params.agent_type) {
                let repl_ready = wait_for_repl_idle(config, &pane_id, ready_timeout)?;
                if !repl_ready {
                    let final_frame = match tmux_capture(config, &pane_id, 40) {
                        Ok(frame) => frame,
                        Err(error) => format!("<final pane capture failed: {error:#}>"),
                    };
                    let screen_tail = final_frame
                        .lines()
                        .rev()
                        .take(15)
                        .collect::<Vec<_>>()
                        .into_iter()
                        .rev()
                        .collect::<Vec<_>>()
                        .join("\n");
                    let reason = format!(
                        "session.launch: {} REPL did not become ready within {}s — startup is \
                         blocked by an unrecognized screen. The prompt was NOT delivered and \
                         the created session cleanup was requested. Last screen tail:\n{}",
                        params.agent_type,
                        ready_timeout.as_secs_f64(),
                        screen_tail,
                    );
                    let failed_at = now_iso();
                    snapshot.status = String::from("failed");
                    snapshot.last_observed_at = Some(failed_at.clone());
                    snapshot.finished_at = Some(failed_at.clone());
                    snapshot.output_snippet = Some(tail_chars(&screen_tail, 500));
                    snapshot.last_validation_error = Some(reason.clone());
                    set_terminal_cause_if_absent(
                        &mut snapshot,
                        TerminalCauseCategory::TimedOut,
                        &reason,
                        true,
                        &failed_at,
                    );
                    // Persist terminality before cleanup: even a tmux cleanup
                    // failure must never strand a BOOTING row indefinitely.
                    upsert_snapshot(conn, &snapshot)?;
                    match tmux_kill_session(config, &params.session_name) {
                        Ok(()) => {
                            snapshot.cleaned_at = Some(failed_at);
                            upsert_snapshot(conn, &snapshot)?;
                        }
                        Err(error) => {
                            eprintln!(
                                "doeff-agentd: failed to clean up timed-out session {}: {error:#}",
                                params.session_id
                            );
                        }
                    }
                    record_command(
                        conn,
                        Some(&snapshot.session_id),
                        "session.launch",
                        "failed",
                        Some(&reason),
                        &snapshot,
                    )?;
                    record_event(
                        conn,
                        &snapshot.session_id,
                        "session_launch_timeout",
                        &snapshot,
                    )?;
                    return Err(anyhow!(reason));
                }
            }
            tmux_send_keys(config, &pane_id, &full_prompt, true, true)?;
            awaiting_response = true;
        }
    }
    if params
        .prompt
        .as_deref()
        .is_none_or(|prompt| prompt.trim().is_empty())
    {
        snapshot.status = String::from("running");
    }
    snapshot.awaiting_response = awaiting_response;
    upsert_snapshot(conn, &snapshot)?;
    record_command(
        conn,
        Some(&snapshot.session_id),
        "session.launch",
        "completed",
        None,
        &snapshot,
    )?;
    Ok(snapshot)
}

fn session_get(conn: &Connection, session_id: &str) -> Result<Option<SessionSnapshot>> {
    conn.query_row(
        "SELECT session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status,
                backend_kind, backend_ref_json, started_at, last_observed_at,
                finished_at, cleaned_at, pr_url, output_snippet,
                terminal_cause_json, expected_result_json, retries_used, last_validation_error,
                awaiting_response, observed_active_at, result_payload_json,
                result_solicitations_used, prompt_unblock_attempts, last_output_change_at
         FROM agent_sessions WHERE session_id = ?1",
        params![session_id],
        row_to_snapshot,
    )
    .optional()
    .map_err(Into::into)
}

fn session_list(conn: &Connection, query: ListParams) -> Result<Vec<SessionSnapshot>> {
    let mut stmt = conn.prepare(
        "SELECT session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status,
                backend_kind, backend_ref_json, started_at, last_observed_at,
                finished_at, cleaned_at, pr_url, output_snippet,
                terminal_cause_json, expected_result_json, retries_used, last_validation_error,
                awaiting_response, observed_active_at, result_payload_json,
                result_solicitations_used, prompt_unblock_attempts, last_output_change_at
         FROM agent_sessions
         ORDER BY started_at DESC, session_id ASC",
    )?;
    let rows = stmt.query_map([], row_to_snapshot)?;
    let mut snapshots = Vec::new();
    for row in rows {
        let snapshot = row?;
        if list_query_matches(&snapshot, &query) {
            snapshots.push(snapshot);
        }
    }
    Ok(snapshots)
}

fn list_query_matches(snapshot: &SessionSnapshot, query: &ListParams) -> bool {
    if let Some(statuses) = &query.status {
        if !statuses.iter().any(|status| status == &snapshot.status) {
            return false;
        }
    }
    if let Some(agent_type) = &query.agent_type {
        if agent_type != &snapshot.agent_type {
            return false;
        }
    }
    if let Some(backend_kind) = &query.backend_kind {
        if backend_kind != &snapshot.backend_kind {
            return false;
        }
    }
    if let Some(lifecycle) = &query.lifecycle {
        if lifecycle != &snapshot.lifecycle {
            return false;
        }
    }
    true
}

fn count_active_sessions(conn: &Connection) -> Result<usize> {
    let active = session_list(
        conn,
        ListParams {
            status: Some(active_statuses()),
            agent_type: None,
            backend_kind: None,
            lifecycle: None,
        },
    )?;
    Ok(active.len())
}

fn active_statuses() -> Vec<String> {
    vec![
        String::from("pending"),
        String::from("booting"),
        String::from("running"),
        String::from("blocked"),
        String::from("blocked_api"),
    ]
}

fn session_capture(conn: &Connection, config: &Config, params: CaptureParams) -> Result<String> {
    let snapshot = require_session(conn, &params.session_id)?;
    let text = tmux_capture(config, &snapshot.pane_id, params.lines)?;
    let output_snippet = tail_chars(&text, 500);
    let mut updated = snapshot.clone();
    updated.output_snippet = Some(output_snippet);
    updated.last_observed_at = Some(now_iso());
    upsert_snapshot(conn, &updated)?;
    record_event(conn, &updated.session_id, "session_captured", &updated)?;
    Ok(text)
}

fn session_send(conn: &Connection, config: &Config, params: SendParams) -> Result<()> {
    let snapshot = require_session(conn, &params.session_id)?;
    tmux_send_keys(
        config,
        &snapshot.pane_id,
        &params.message,
        params.literal,
        params.enter,
    )?;
    record_command(
        conn,
        Some(&snapshot.session_id),
        "session.send",
        "completed",
        None,
        &params.message,
    )?;
    record_event(conn, &snapshot.session_id, "session_sent", &snapshot)?;
    Ok(())
}

fn session_cancel(conn: &Connection, config: &Config, session_id: &str) -> Result<SessionSnapshot> {
    let mut snapshot = require_session(conn, session_id)?;
    if tmux_has_session(config, &snapshot.session_name)? {
        tmux_kill_session(config, &snapshot.session_name)?;
    }
    let now = now_iso();
    snapshot.status = String::from("stopped");
    snapshot.finished_at = Some(now.clone());
    snapshot.last_observed_at = Some(now.clone());
    set_terminal_cause_if_absent(
        &mut snapshot,
        TerminalCauseCategory::Cancelled,
        "session.cancel requested",
        false,
        &now,
    );
    upsert_snapshot(conn, &snapshot)?;
    record_command(
        conn,
        Some(session_id),
        "session.cancel",
        "completed",
        None,
        &snapshot,
    )?;
    record_event(conn, session_id, "session_cancelled", &snapshot)?;
    Ok(snapshot)
}

fn session_cleanup(
    conn: &Connection,
    config: &Config,
    session_id: &str,
) -> Result<SessionSnapshot> {
    let mut snapshot = require_session(conn, session_id)?;
    if tmux_has_session(config, &snapshot.session_name)? {
        tmux_kill_session(config, &snapshot.session_name)?;
    }
    let now = now_iso();
    if !is_terminal_status(&snapshot.status) {
        snapshot.status = String::from("stopped");
        set_terminal_cause_if_absent(
            &mut snapshot,
            TerminalCauseCategory::Cancelled,
            "session.cleanup stopped a non-terminal session",
            false,
            &now,
        );
    }
    snapshot.finished_at.get_or_insert_with(|| now.clone());
    snapshot.cleaned_at = Some(now.clone());
    snapshot.last_observed_at = Some(now);
    upsert_snapshot(conn, &snapshot)?;
    record_command(
        conn,
        Some(session_id),
        "session.cleanup",
        "completed",
        None,
        &snapshot,
    )?;
    record_event(conn, session_id, "session_cleaned", &snapshot)?;
    Ok(snapshot)
}

/// Block until the named session reaches a terminal status (or the
/// caller-supplied timeout elapses).  Built for the Haskell agent-
/// control-plane daemon: clients must not scrape terminal output or
/// side-channel files themselves. This RPC consolidates the wait +
/// validation handoff into a single response so callers never see an
/// unvalidated payload.
///
/// Threading note: each agentd connection runs in its own thread (see
/// `serve` / `handle_stream`), so blocking the calling thread here
/// does not stall the rest of the daemon — other RPCs continue to be
/// served concurrently.
fn session_await_result(conn: &Connection, params: AwaitResultParams) -> Result<Value> {
    session_await_result_with_interval(conn, params, Duration::from_millis(AWAIT_POLL_INTERVAL_MS))
}

/// Test-friendly variant of `session_await_result` that exposes the
/// polling cadence.  Production code uses `AWAIT_POLL_INTERVAL_MS`;
/// tests override it to keep total runtime small.
fn session_await_result_with_interval(
    conn: &Connection,
    params: AwaitResultParams,
    poll_interval: Duration,
) -> Result<Value> {
    let timeout_seconds = params
        .timeout_seconds
        .unwrap_or(DEFAULT_AWAIT_TIMEOUT_SECONDS)
        .clamp(MIN_AWAIT_TIMEOUT_SECONDS, MAX_AWAIT_TIMEOUT_SECONDS);
    let timeout = Duration::from_secs_f64(timeout_seconds);
    let started = std::time::Instant::now();

    // Probe once up front so a missing session fails fast with the
    // dedicated -32001 code instead of waiting for the timeout.
    let initial = session_get(conn, &params.session_id)?;
    let mut snapshot = match initial {
        Some(snap) => snap,
        None => {
            return Err(anyhow::Error::new(RpcError {
                code: RPC_ERR_NO_SUCH_SESSION,
                message: format!("no session with id '{}'", params.session_id),
            }));
        }
    };

    loop {
        if is_await_terminal_status(&snapshot.status) {
            return Ok(build_await_response(&snapshot));
        }
        if started.elapsed() >= timeout {
            return Err(anyhow::Error::new(RpcError {
                code: RPC_ERR_AWAIT_TIMEOUT,
                message: format!(
                    "session.await_result timed out after {}s for session '{}'",
                    timeout_seconds as u64, params.session_id
                ),
            }));
        }
        thread::sleep(poll_interval);
        snapshot = match session_get(conn, &params.session_id)? {
            Some(snap) => snap,
            None => {
                // The session row vanished mid-wait — surface the same
                // dedicated error code as the initial-not-found case
                // so the Haskell client can branch identically.
                return Err(anyhow::Error::new(RpcError {
                    code: RPC_ERR_NO_SUCH_SESSION,
                    message: format!("no session with id '{}'", params.session_id),
                }));
            }
        };
    }
}

/// Assemble the success response for `session.await_result`.  The
/// `result` field is `Some(...)` only when the session reached `done`
/// AND its `expected_result` contract validates successfully; in
/// every other terminal state (including `failed` due to validation
/// timeout) `result` is null and `validation_error` carries whichever
/// reason the monitor recorded.
/// Handle `session.report_result` (ADR 0035): the agent delivered its
/// structured result over the agentd-owned MCP data channel. Validate it
/// against the session's contract and, if valid, persist it byte-faithfully.
///
/// Concurrency: `report_result` is the SOLE writer of `result_payload_json`,
/// written via a guarded UPDATE (first-write-wins, only while the session is
/// non-terminal). It NEVER writes `status`; the monitor stays the sole
/// status writer and flips the session to `done` once it observes the
/// persisted result. `upsert_snapshot` COALESCE-preserves
/// `result_payload_json`, so the monitor's routine observation upserts can
/// never clobber a reported result.
///
/// A schema-invalid payload is a deterministic failure: it is NOT persisted
/// and NOT re-validated (hard rule 7 / ADR 0035 R4). The reason is returned
/// to the agent so it can correct the payload within the same session; if
/// the session then reaches turn-end without a valid result, the monitor's
/// bounded solicitation loop takes over (ADR-DOE-AGENTS-002 R3).
fn session_report_result(conn: &Connection, params: ReportResultParams) -> Result<Value> {
    let snapshot = require_session(conn, &params.session_id)?;
    if is_terminal_status(&snapshot.status) {
        if snapshot.result_payload.is_some() {
            // Idempotent: the result already landed before the session
            // finalised; a duplicate report is not an error.
            return Ok(json!({"accepted": true, "already_reported": true}));
        }
        return Err(anyhow::Error::new(RpcError {
            code: RPC_ERR_ALREADY_TERMINAL,
            message: format!(
                "session '{}' already reached terminal status '{}' without a result",
                params.session_id, snapshot.status
            ),
        }));
    }
    let spec = snapshot.expected_result.as_ref().ok_or_else(|| {
        anyhow!(
            "session '{}' has no result contract; {REPORT_RESULT_TOOL} is not applicable",
            params.session_id
        )
    })?;
    if let Err(reason) = validate_against_schema(&params.payload, &spec.payload_schema, "payload") {
        // Deterministic validation failure: record for audit, tell the
        // agent why, do NOT persist and do NOT retry.
        record_event(
            conn,
            &params.session_id,
            "session_result_rejected",
            &json!({"session_id": params.session_id, "reason": reason}),
        )?;
        return Err(anyhow::Error::new(RpcError {
            code: RPC_ERR_RESULT_REJECTED,
            message: format!("reported result does not satisfy its schema: {reason}"),
        }));
    }
    // Byte-faithful persistence: serialize the exact JSON value the agent
    // sent. Guarded UPDATE = first-write-wins while the session is not yet
    // terminal.
    let payload_json = serde_json::to_string(&params.payload)?;
    let affected = conn.execute(
        "UPDATE agent_sessions SET result_payload_json = ?1 \
         WHERE session_id = ?2 AND result_payload_json IS NULL \
           AND status NOT IN ('done','failed','exited','stopped','cancelled')",
        params![payload_json, params.session_id],
    )?;
    if affected == 0 {
        // Either a result already landed (idempotent) or the session went
        // terminal between the checks above and the UPDATE.
        match session_get(conn, &params.session_id)? {
            Some(s) if s.result_payload.is_some() => {
                return Ok(json!({"accepted": true, "already_reported": true}));
            }
            _ => {
                return Err(anyhow::Error::new(RpcError {
                    code: RPC_ERR_ALREADY_TERMINAL,
                    message: format!(
                        "session '{}' finished before the result could be recorded",
                        params.session_id
                    ),
                }));
            }
        }
    }
    record_event(
        conn,
        &params.session_id,
        "session_result_reported",
        &json!({"session_id": params.session_id}),
    )?;
    Ok(json!({"accepted": true}))
}

fn build_await_response(snapshot: &SessionSnapshot) -> Value {
    let mut response = serde_json::Map::new();
    response.insert(
        String::from("session"),
        serde_json::to_value(snapshot).unwrap_or(Value::Null),
    );

    let mut result_value: Value = Value::Null;
    let mut validation_error: Option<String> = snapshot.last_validation_error.clone();

    if snapshot.status == "done" {
        if snapshot.expected_result.is_some() {
            // ADR 0035: the ONLY result source is the payload the agent
            // delivered over the `report_result` data channel and agentd
            // persisted byte-faithfully. There is no transcript fallback —
            // scraping the screen is exactly the non-injective projection
            // this ADR removes. `report_result` only ever persists a
            // schema-valid payload, so a `done` contract session always
            // has one; a missing payload is a bug, surfaced as an error.
            let stored = snapshot
                .result_payload
                .as_ref()
                .and_then(|raw| serde_json::from_str::<Value>(raw).ok());
            match stored {
                Some(parsed) => {
                    let mut result_obj = serde_json::Map::new();
                    result_obj.insert(String::from("payload"), parsed);
                    result_value = Value::Object(result_obj);
                    validation_error = None;
                }
                None => {
                    if validation_error.is_none() {
                        validation_error = Some(String::from(
                            "session reached 'done' without a reported result payload",
                        ));
                    }
                }
            }
        }
    }

    response.insert(String::from("result"), result_value);
    if let Some(reason) = validation_error {
        response.insert(String::from("validation_error"), Value::String(reason));
    }
    Value::Object(response)
}

fn require_session(conn: &Connection, session_id: &str) -> Result<SessionSnapshot> {
    session_get(conn, session_id)?.ok_or_else(|| anyhow!("session is not registered: {session_id}"))
}

fn row_to_snapshot(row: &rusqlite::Row<'_>) -> rusqlite::Result<SessionSnapshot> {
    let backend_ref_json: String = row.get(8)?;
    let backend_ref =
        serde_json::from_str::<BTreeMap<String, String>>(&backend_ref_json).map_err(|err| {
            rusqlite::Error::FromSqlConversionFailure(8, rusqlite::types::Type::Text, Box::new(err))
        })?;
    let terminal_cause_json: Option<String> = row.get(15)?;
    let terminal_cause = match terminal_cause_json {
        Some(json) => Some(serde_json::from_str::<TerminalCause>(&json).map_err(|err| {
            rusqlite::Error::FromSqlConversionFailure(
                15,
                rusqlite::types::Type::Text,
                Box::new(err),
            )
        })?),
        None => None,
    };
    let expected_result_json: Option<String> = row.get(16)?;
    let expected_result = match expected_result_json {
        Some(json) => Some(
            serde_json::from_str::<ExpectedResultSpec>(&json).map_err(|err| {
                rusqlite::Error::FromSqlConversionFailure(
                    16,
                    rusqlite::types::Type::Text,
                    Box::new(err),
                )
            })?,
        ),
        None => None,
    };
    Ok(SessionSnapshot {
        session_id: row.get(0)?,
        session_name: row.get(1)?,
        pane_id: row.get(2)?,
        agent_type: row.get(3)?,
        work_dir: row.get(4)?,
        lifecycle: row.get(5)?,
        status: row.get(6)?,
        backend_kind: row.get(7)?,
        backend_ref,
        started_at: row.get(9)?,
        last_observed_at: row.get(10)?,
        finished_at: row.get(11)?,
        cleaned_at: row.get(12)?,
        pr_url: row.get(13)?,
        output_snippet: row.get(14)?,
        terminal_cause,
        expected_result,
        retries_used: row.get::<_, i64>(17)? as u32,
        last_validation_error: row.get(18)?,
        awaiting_response: row.get::<_, i64>(19)? != 0,
        observed_active_at: row.get(20)?,
        result_payload: row.get(21)?,
        result_solicitations_used: row.get::<_, i64>(22)? as u32,
        prompt_unblock_attempts: row.get::<_, i64>(23)? as u32,
        last_output_change_at: row.get(24)?,
    })
}

fn upsert_snapshot(conn: &Connection, snapshot: &SessionSnapshot) -> Result<()> {
    let backend_ref_json = serde_json::to_string(&snapshot.backend_ref)?;
    let terminal_cause_json = match &snapshot.terminal_cause {
        Some(cause) => Some(serde_json::to_string(cause)?),
        None => None,
    };
    let expected_result_json = match &snapshot.expected_result {
        Some(spec) => Some(serde_json::to_string(spec)?),
        None => None,
    };
    conn.execute(
        "INSERT INTO agent_sessions (
            session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status,
            backend_kind, backend_ref_json, started_at, last_observed_at,
            finished_at, cleaned_at, pr_url, output_snippet,
            terminal_cause_json, expected_result_json, retries_used, last_validation_error,
            awaiting_response, observed_active_at, result_payload_json,
            result_solicitations_used, prompt_unblock_attempts, last_output_change_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?24, ?25)
         ON CONFLICT(session_id) DO UPDATE SET
            session_name = excluded.session_name,
            pane_id = excluded.pane_id,
            agent_type = excluded.agent_type,
            work_dir = excluded.work_dir,
            lifecycle = excluded.lifecycle,
            status = excluded.status,
            backend_kind = excluded.backend_kind,
            backend_ref_json = excluded.backend_ref_json,
            started_at = excluded.started_at,
            last_observed_at = excluded.last_observed_at,
            finished_at = excluded.finished_at,
            cleaned_at = excluded.cleaned_at,
            pr_url = excluded.pr_url,
            output_snippet = excluded.output_snippet,
            terminal_cause_json = COALESCE(agent_sessions.terminal_cause_json, excluded.terminal_cause_json),
            expected_result_json = excluded.expected_result_json,
            retries_used = excluded.retries_used,
            last_validation_error = excluded.last_validation_error,
            awaiting_response = excluded.awaiting_response,
            observed_active_at = excluded.observed_active_at,
            result_payload_json = COALESCE(agent_sessions.result_payload_json, excluded.result_payload_json),
            result_solicitations_used = excluded.result_solicitations_used,
            prompt_unblock_attempts = excluded.prompt_unblock_attempts,
            last_output_change_at = excluded.last_output_change_at",
        params![
            snapshot.session_id,
            snapshot.session_name,
            snapshot.pane_id,
            snapshot.agent_type,
            snapshot.work_dir,
            snapshot.lifecycle,
            snapshot.status,
            snapshot.backend_kind,
            backend_ref_json,
            snapshot.started_at,
            snapshot.last_observed_at,
            snapshot.finished_at,
            snapshot.cleaned_at,
            snapshot.pr_url,
            snapshot.output_snippet,
            terminal_cause_json,
            expected_result_json,
            i64::from(snapshot.retries_used),
            snapshot.last_validation_error,
            i64::from(snapshot.awaiting_response),
            snapshot.observed_active_at,
            snapshot.result_payload,
            i64::from(snapshot.result_solicitations_used),
            i64::from(snapshot.prompt_unblock_attempts),
            snapshot.last_output_change_at,
        ],
    )?;
    Ok(())
}

fn record_event<T: Serialize>(
    conn: &Connection,
    session_id: &str,
    event_type: &str,
    payload: &T,
) -> Result<()> {
    conn.execute(
        "INSERT INTO agent_session_events
          (session_id, event_type, occurred_at, payload_json)
         VALUES (?1, ?2, ?3, ?4)",
        params![
            session_id,
            event_type,
            now_iso(),
            serde_json::to_string(payload)?,
        ],
    )?;
    Ok(())
}

fn record_command<T: Serialize>(
    conn: &Connection,
    session_id: Option<&str>,
    command_type: &str,
    status: &str,
    error: Option<&str>,
    payload: &T,
) -> Result<()> {
    let now = now_iso();
    conn.execute(
        "INSERT INTO agent_session_commands
          (session_id, command_type, requested_at, completed_at, status, payload_json, error)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            session_id,
            command_type,
            now,
            now,
            status,
            serde_json::to_string(payload)?,
            error,
        ],
    )?;
    Ok(())
}

/// Baseline environment injected into every agent tmux session so an
/// interactive shell-STARTUP prompt cannot derail the agent we are about to
/// drive with `send-keys`.  These vars are set in the tmux session environment
/// (via `new-session -e`), so the spawned shell inherits them BEFORE it sources
/// its rc — that is the only point early enough to suppress a prompt that fires
/// during shell init.  A blocked `[y/N]` at startup eats the launch keystrokes
/// (the agent command is typed into the prompt, not the shell) and the session
/// never starts; agentd cannot answer it (its only channel is `send-keys`,
/// which is what the prompt is stealing).  Each var is harmless on shells /
/// frameworks that don't recognise it (just an unused export).  Caller-supplied
/// `session_env` overrides any key here.
///   * DISABLE_AUTO_UPDATE / DISABLE_UPDATE_PROMPT — oh-my-zsh's "[oh-my-zsh]
///     Would you like to update? [Y/n]" auto-update reminder at shell startup.
/// (The agent's OWN update dialog — e.g. codex's "Update available!" — is a
/// separate, in-app prompt handled after launch by `dismiss_codex_update_dialog`.)
const SHELL_PROMPT_SUPPRESSING_ENV: &[(&str, &str)] = &[
    ("DISABLE_AUTO_UPDATE", "true"),
    ("DISABLE_UPDATE_PROMPT", "true"),
];

const FORBIDDEN_AGENT_ENV_KEYS: &[&str] = &[
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_PERSONAL",
    "ANTHROPIC_API_KEY__PERSONAL",
];

fn normalized_env_key(key: &str) -> String {
    key.replace('-', "_").to_ascii_uppercase()
}

fn forbidden_agent_env_keys(env_vars: &BTreeMap<String, String>) -> Vec<String> {
    env_vars
        .keys()
        .filter(|key| {
            let normalized = normalized_env_key(key);
            FORBIDDEN_AGENT_ENV_KEYS
                .iter()
                .any(|forbidden| normalized == *forbidden)
        })
        .cloned()
        .collect()
}

fn ensure_no_forbidden_agent_env(env_vars: &BTreeMap<String, String>) -> Result<()> {
    let forbidden = forbidden_agent_env_keys(env_vars);
    if forbidden.is_empty() {
        return Ok(());
    }
    Err(anyhow!(
        "doeff-agentd must never pass Anthropic API keys to agent processes. \
         API-key-backed calls are allowed only through memoized LLMStructuredQuery / \
         StructuredLLMQuery handlers, never agent session environments. \
         Forbidden key(s): {}",
        forbidden.join(", ")
    ))
}

/// The ordered `KEY=VALUE` env entries to set on a new agent session: the
/// baseline prompt-suppressors first (skipped when the caller overrides that
/// key), then the caller's own `session_env`.  Pure so the merge/override
/// behaviour is unit-tested without a live tmux.
fn session_env_entries(env_vars: &BTreeMap<String, String>) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for (key, value) in SHELL_PROMPT_SUPPRESSING_ENV {
        if !env_vars.contains_key(*key) {
            out.push(((*key).to_string(), (*value).to_string()));
        }
    }
    for (key, value) in env_vars {
        out.push((key.clone(), value.clone()));
    }
    out
}

fn tmux_new_session(
    config: &Config,
    session_name: &str,
    work_dir: &str,
    env_vars: &BTreeMap<String, String>,
) -> Result<String> {
    ensure_no_forbidden_agent_env(env_vars)?;
    let mut command = Command::new(&config.tmux_bin);
    command.args(["new-session", "-d", "-s", session_name, "-P", "-F", "#D"]);
    command.args(["-c", work_dir]);
    for (key, value) in session_env_entries(env_vars) {
        command.args(["-e", &format!("{key}={value}")]);
    }
    let output = command.output().context("tmux new-session failed to run")?;
    if !output.status.success() {
        return Err(anyhow!(
            "tmux new-session failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn tmux_has_session(config: &Config, session_name: &str) -> Result<bool> {
    let status = Command::new(&config.tmux_bin)
        .args(["has-session", "-t", session_name])
        .status()
        .context("tmux has-session failed to run")?;
    Ok(status.success())
}

fn tmux_send_keys(
    config: &Config,
    target: &str,
    message: &str,
    literal: bool,
    enter: bool,
) -> Result<()> {
    if literal && !message.is_empty() {
        tmux_paste_literal(config, target, message)?;
    } else {
        let mut command = Command::new(&config.tmux_bin);
        command.args(["send-keys", "-t", target]);
        if literal {
            command.args(["-l", message]);
        } else {
            command.arg(message);
        }
        let status = command.status().context("tmux send-keys failed to run")?;
        if !status.success() {
            return Err(anyhow!("tmux send-keys failed"));
        }
    }
    if enter {
        // codex renders the input box character-by-character; if we
        // press Enter the same millisecond the last byte of the
        // prompt lands, the keystroke can arrive while the UI is
        // still in a transient state and get silently dropped, leaving
        // the text sitting in the input forever.  A short pause gives
        // codex time to settle into the "prompt ready, awaiting
        // submit" state before the Enter is delivered.
        thread::sleep(Duration::from_millis(1000));
        tmux_send_enter(config, target)?;
        if literal && !message.is_empty() {
            confirm_literal_prompt_submitted(config, target, message)?;
        }
    }
    Ok(())
}

fn tmux_send_enter(config: &Config, target: &str) -> Result<()> {
    let enter_status = Command::new(&config.tmux_bin)
        .args(["send-keys", "-t", target, "Enter"])
        .status()
        .context("tmux send Enter failed to run")?;
    if !enter_status.success() {
        return Err(anyhow!("tmux send Enter failed"));
    }
    Ok(())
}

fn confirm_literal_prompt_submitted(config: &Config, target: &str, message: &str) -> Result<()> {
    // Claude Code and Codex can collapse large pasted prompts into
    // `[Pasted text ...]` / `[Pasted Content ...]`.
    // On slower terminals, the submit Enter may be dropped after the paste,
    // leaving that collapsed paste marker, or the visible tail of the pasted
    // prompt, sitting in the input box forever. Detect that state and resend
    // Enter a few times; this keeps the launch contract terminal-driven
    // without falling back to one-shot print mode or stdin delivery.
    thread::sleep(Duration::from_millis(1200));
    for _ in 0..3 {
        let output = tmux_capture(config, target, 40)?;
        if !output_has_unsubmitted_paste_input(&output, Some(message)) {
            return Ok(());
        }
        tmux_send_enter(config, target)?;
        thread::sleep(Duration::from_millis(1000));
    }
    Ok(())
}

fn output_has_unsubmitted_paste_input(output: &str, sent_text: Option<&str>) -> bool {
    let lines: Vec<&str> = output.lines().collect();
    let start = lines.len().saturating_sub(20);
    let recent = &lines[start..];
    let mut last_prompt_line: Option<&str> = None;
    let mut last_prompt_index: Option<usize> = None;
    for (index, line) in recent.iter().enumerate() {
        let trimmed = line.trim_start();
        if trimmed.starts_with('❯') || trimmed.starts_with('›') {
            last_prompt_line = Some(trimmed);
            last_prompt_index = Some(index);
        }
    }
    if last_prompt_line
        .map(|line| {
            line.contains("[Pasted text")
                || line.contains("[Pasted Content")
                || line.contains("Press up to edit queued messages")
        })
        .unwrap_or(false)
    {
        return true;
    }
    let Some(sent_text) = sent_text else {
        return false;
    };
    let Some(prompt_index) = last_prompt_index else {
        return false;
    };
    let prompt_region = normalize_prompt_text(&recent[prompt_index..].join("\n"));
    let prompt_region_compact = compact_prompt_text(&prompt_region);
    literal_prompt_fragments(sent_text)
        .into_iter()
        .any(|fragment| {
            if prompt_region.contains(&fragment) {
                return true;
            }
            let compact_fragment = compact_prompt_text(&fragment);
            compact_fragment.chars().count() >= 24
                && prompt_region_compact.contains(&compact_fragment)
        })
}

fn normalize_prompt_text(text: &str) -> String {
    text.replace('\u{00A0}', " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn compact_prompt_text(text: &str) -> String {
    normalize_prompt_text(text)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join("")
}

fn literal_prompt_fragments(text: &str) -> Vec<String> {
    let normalized = normalize_prompt_text(text);
    let words: Vec<&str> = normalized.split_whitespace().collect();
    let mut fragments = Vec::new();
    for start in 0..words.len().saturating_sub(3) {
        let fragment = words[start..start + 4].join(" ");
        if fragment.chars().count() >= 24 {
            fragments.push(fragment);
        }
    }
    let char_count = normalized.chars().count();
    if char_count >= 24 {
        fragments.push(normalized.chars().take(80).collect());
        fragments.push(
            normalized
                .chars()
                .skip(char_count.saturating_sub(80))
                .collect(),
        );
    }
    fragments
}

fn tmux_paste_literal(config: &Config, target: &str, message: &str) -> Result<()> {
    // Long, multi-line prompts are fragile through `send-keys -l`: real
    // Claude Code has dropped them while staying at an empty prompt.  Keep the
    // prompt in the live terminal transport, but paste the text through tmux's
    // buffer and use send-keys only for the submit Enter.
    let buffer_name = format!(
        "doeff-agentd-{}-{}",
        std::process::id(),
        target
            .chars()
            .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
            .collect::<String>()
    );
    // The buffer content goes through load-buffer's STDIN, never through the
    // command line: tmux's client-server protocol caps a single command at
    // ~16KB (imsg framing), so `set-buffer -b <name> <message>` fails with
    // "command too long" once the message outgrows that — observed live when
    // argus attend prompts crossed the threshold and every launch died at
    // paste. stdin streaming has no such cap (verified to 5MB).
    let mut load = Command::new(&config.tmux_bin)
        .args(["load-buffer", "-b", &buffer_name, "-"])
        .stdin(Stdio::piped())
        .spawn()
        .context("tmux load-buffer failed to run")?;
    load.stdin
        .as_mut()
        .context("tmux load-buffer stdin unavailable")?
        .write_all(message.as_bytes())
        .context("tmux load-buffer stdin write failed")?;
    let load_status = load.wait().context("tmux load-buffer failed to run")?;
    if !load_status.success() {
        return Err(anyhow!("tmux load-buffer failed"));
    }
    // -p = bracketed paste: raw newlines reach the agent TUI as bare Enter
    // presses and a cold-start multi-line prompt splits into per-line
    // submits (issue agentd-codex-coldstart-paste-race, 2026-07-14).
    let paste_status = Command::new(&config.tmux_bin)
        .args(["paste-buffer", "-p", "-b", &buffer_name, "-t", target])
        .status()
        .context("tmux paste-buffer failed to run")?;
    let _ = Command::new(&config.tmux_bin)
        .args(["delete-buffer", "-b", &buffer_name])
        .status();
    if !paste_status.success() {
        return Err(anyhow!("tmux paste-buffer failed"));
    }
    Ok(())
}

fn tmux_capture(config: &Config, target: &str, lines: i64) -> Result<String> {
    let start = format!("-{}", lines.max(1));
    let output = Command::new(&config.tmux_bin)
        .args(["capture-pane", "-t", target, "-p", "-J", "-S", &start])
        .output()
        .context("tmux capture-pane failed to run")?;
    if !output.status.success() {
        return Err(anyhow!(
            "tmux capture-pane failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn tmux_kill_session(config: &Config, session_name: &str) -> Result<()> {
    let status = Command::new(&config.tmux_bin)
        .args(["kill-session", "-t", session_name])
        .status()
        .context("tmux kill-session failed to run")?;
    if !status.success() {
        return Err(anyhow!("tmux kill-session failed: {}", session_name));
    }
    Ok(())
}

/// Read tmux's `pane_current_command` for a pane.  Returns `Ok(None)`
/// when the pane is missing (tmux exits non-zero) — callers treat that
/// the same as "session went away".
fn tmux_pane_current_command(config: &Config, pane_id: &str) -> Result<Option<String>> {
    let output = Command::new(&config.tmux_bin)
        .args([
            "display-message",
            "-p",
            "-t",
            pane_id,
            "#{pane_current_command}",
        ])
        .output()
        .context("tmux display-message failed to run")?;
    if !output.status.success() {
        return Ok(None);
    }
    Ok(Some(
        String::from_utf8_lossy(&output.stdout).trim().to_string(),
    ))
}

/// Names of interactive shells we treat as "no agent is currently
/// running in this pane".  When the pane's foreground process drops
/// back to one of these, the codex / claude binary the launcher
/// originally started has exited and tmux is just keeping the pane
/// alive against its parent shell.  See 'pane_looks_like_idle_shell'.
const IDLE_SHELL_COMMANDS: &[&str] = &["zsh", "bash", "sh", "dash", "fish", "ksh"];

/// True when the pane's foreground process is one of the interactive
/// shells we recognise as "no agent here anymore".  We deliberately
/// list shells explicitly rather than blacklisting known agent
/// commands: codex / claude may legitimately fork short-lived helpers
/// (git, gh, jq, etc.) that briefly become the pane's current
/// command — those must not be misclassified as "agent gone".
fn pane_looks_like_idle_shell(current_command: &str) -> bool {
    IDLE_SHELL_COMMANDS
        .iter()
        .any(|shell| current_command == *shell)
}

/// Classify the agent's output into a coarse status.
///
/// This function never returns @done@ from a heuristic — work-end is
/// decided in 'monitor_once' after the input→output contract has been
/// validated.  The signals that this function *does* return are:
///
/// * @failed@ — output contains a hard-failure marker we recognise.
/// * @blocked_api@ — provider rate-limit / quota message.
/// * @blocked@ — agent is asking the user a question or waiting on
///   interactive permission.
/// * @running@ — anything else, including codex's per-turn "Worked for X"
///   status display. That marker is a *turn-end* signal, not a work-end
///   signal: see 'output_indicates_turn_end' and the monitor's
///   contract-validation block.
fn observed_status_for_snapshot(snapshot: &SessionSnapshot, output: &str) -> &'static str {
    if output_has_failure_marker(output) {
        return "failed";
    }
    if output_has_api_limit_marker(output) {
        return "blocked_api";
    }
    if output_has_waiting_marker(output) {
        return "blocked";
    }
    // For Kind 1 (Interactive) the agent simply sits at the idle
    // prompt between turns; for Kind 2 (RunToCompletion) the turn
    // end is interpreted by 'monitor_once' against the contract.
    // Either way the *status* the snapshot carries until that
    // decision is "running".
    let _ = snapshot;
    "running"
}

fn launch_transport_owns_snapshot(snapshot: &SessionSnapshot) -> bool {
    snapshot.status == "booting" && !snapshot.awaiting_response
}

/// True when codex has visibly finished one turn — the idle @›@
/// prompt is showing, the agent is not currently working, and the
/// pane content is stable since the last observation.  Used by
/// 'monitor_once' as a trigger to evaluate the input→output
/// contract for Kind 2 sessions.
///
/// We deliberately do *not* require the "Worked for X" status line
/// to be visible: that text scrolls out of the pane on long
/// sessions, so a session that genuinely finished a turn hours ago
/// would otherwise sit forever in "running" even though it is
/// clearly parked at the idle prompt.  False positives at launch
/// are caught by the stability check — codex's banner and MCP
/// loading produce non-stable output for the first few polls.
///
/// A turn ending is **not** the same as the work ending: a single
/// prompt may take several turns to complete, and Kind 1 sessions
/// keep serving turns indefinitely.  We only graduate to a terminal
/// status when (a) Kind 2 and (b) the contract validates.
fn output_indicates_turn_end(snapshot: &SessionSnapshot, output: &str) -> bool {
    let idle = output_has_agent_idle_prompt(output) && !output_has_agent_active_marker(output);
    let stable = output_is_stable(snapshot, output);
    idle && stable
}

fn should_cleanup_after_observed_status(snapshot: &SessionSnapshot, status: &str) -> bool {
    is_run_to_completion_lifecycle(&snapshot.lifecycle) && (status == "done" || status == "failed")
}

fn is_terminal_status(status: &str) -> bool {
    status == "done"
        || status == "failed"
        || status == "exited"
        || status == "stopped"
        || status == "cancelled"
}

fn set_terminal_cause_if_absent(
    snapshot: &mut SessionSnapshot,
    category: TerminalCauseCategory,
    reason: impl Into<String>,
    retryable: bool,
    observed_at: &str,
) {
    if snapshot.terminal_cause.is_some() {
        return;
    }
    snapshot.terminal_cause = Some(TerminalCause {
        category,
        reason: Some(reason.into()),
        retryable,
        retry_after_seconds: None,
        backend_error_code: None,
        exit_code: None,
        signal: None,
        observed_at: observed_at.to_string(),
    });
}

fn set_failed_output_cause_if_absent(
    snapshot: &mut SessionSnapshot,
    output: &str,
    observed_at: &str,
) {
    if snapshot.terminal_cause.is_some() {
        return;
    }
    let lower = output_tail_lower(output, 30);
    let (category, retryable) = if output_has_api_limit_marker(output) {
        (TerminalCauseCategory::RateLimited, true)
    } else if lower.contains("timeout") || lower.contains("timed out") || lower.contains("deadline")
    {
        (TerminalCauseCategory::TimedOut, true)
    } else if lower.contains("authentication failed") {
        (TerminalCauseCategory::RunnerUnavailable, false)
    } else if lower.contains("invalid json") || lower.contains("protocol error") {
        (TerminalCauseCategory::ProtocolError, false)
    } else {
        (TerminalCauseCategory::RunFailed, false)
    };
    let reason = tail_chars(output.trim(), 500);
    set_terminal_cause_if_absent(
        snapshot,
        category,
        if reason.is_empty() {
            String::from("agent output indicated failure")
        } else {
            reason
        },
        retryable,
        observed_at,
    );
}

/// Terminal-status check used by `session.await_result`.  Wider than
/// `is_terminal_status` because the await contract documents that a
/// session reaching `cancelled` or `lost` is also a final, no-more-
/// transitions state from the caller's point of view — there is no
/// useful reason to keep blocking once one of those is observed.
fn is_await_terminal_status(status: &str) -> bool {
    matches!(
        status,
        "done" | "failed" | "cancelled" | "exited" | "stopped" | "lost"
    )
}

fn event_type_for_observed_status(status: &str) -> &'static str {
    if status == "done" {
        return "session_done";
    }
    if status == "failed" {
        return "session_failed";
    }
    if status == "blocked" || status == "blocked_api" {
        return "session_blocked";
    }
    "session_observed"
}

fn output_is_stable(snapshot: &SessionSnapshot, output: &str) -> bool {
    if let Some(previous) = &snapshot.output_snippet {
        return previous == &tail_chars(output, 500);
    }
    false
}

fn output_tail_lower(output: &str, max_lines: usize) -> String {
    let lines: Vec<&str> = output.lines().collect();
    let start = lines.len().saturating_sub(max_lines);
    lines[start..].join("\n").to_lowercase()
}

// The previous 'output_has_completion_marker' heuristic was deleted
// because every marker it recognised was either:
//   * a turn-end signal that codex shows after *every* prompt
//     ("worked for"), or
//   * an agent-authored claim of completion that has no relationship
//     to whether the input→output contract has actually been met
//     ("task completed successfully", "goodbye", …).
// Both classes routinely fired before the work was actually done and
// triggered premature cleanup.  Work-end is now decided exclusively
// by 'validate_expected_result' inside 'monitor_once'.

fn output_has_failure_marker(output: &str) -> bool {
    let text = output_tail_lower(output, 10);
    text.contains("fatal error")
        || text.contains("unrecoverable error")
        || text.contains("agent crashed")
        || text.contains("session terminated")
        || text.contains("authentication failed")
}

fn output_has_api_limit_marker(output: &str) -> bool {
    let text = output_tail_lower(output, 30);
    text.contains("cost limit reached")
        || text.contains("rate limit exceeded")
        || text.contains("rate limit reached")
        || text.contains("quota exceeded")
        || text.contains("insufficient quota")
        || text.contains("resource exhausted")
        || text.contains("you've hit your limit")
        || text.contains("/rate-limit-options")
        || text.contains("stop and wait for limit to reset")
}

fn output_has_waiting_marker(output: &str) -> bool {
    output.contains("tell Claude what to do differently")
        || output.contains("Type your message")
        || output.contains("accept edits")
        || output.contains("bypass permissions")
        || output.contains("shift+tab to cycle")
        || output.contains("Esc to cancel")
        || output.contains("to show all projects")
}

fn output_has_agent_idle_prompt(output: &str) -> bool {
    // `› ` is codex's REPL prompt; `❯` is Claude Code's input box.  The
    // claude prompt is visible even DURING active work (the input box sits
    // below the spinner), so idle detection for claude leans entirely on
    // the stability guard in `output_indicates_turn_end`: the spinner's
    // per-second timer tick keeps a working pane unstable.
    //
    // Claude separates `❯` from typed text with a NO-BREAK SPACE (U+00A0),
    // not an ASCII space (observed live: '❯\u{00A0}text'), and an empty
    // input box may render `❯` with nothing after it — so match the glyph
    // at line start alone, never the two-char `❯ `.
    if output.starts_with("› ") || output.contains("\n› ") {
        return true;
    }
    output.lines().any(|line| line.starts_with('❯'))
}

/// "Startup finished" signal for the launch-timeout watchdog: the REPL
/// input box (`› ` for codex, `❯` for claude) renders only once the agent
/// is initialised, and the codex active-work marker implies the same.
/// MCP-boot and update-dialog screens are excluded — those are exactly
/// the stuck-in-startup states the watchdog must keep reaping (the
/// update dialog also renders a `›` selection marker that would
/// otherwise read as a ready REPL).
fn output_indicates_startup_finished(output: &str) -> bool {
    if output_is_starting_mcp_servers(output)
        || output_has_codex_update_dialog(output)
        || output_has_claude_bypass_permissions_dialog(output)
        || output_has_claude_fullscreen_renderer_dialog(output)
    {
        return false;
    }
    output_has_codex_active_marker(output)
        || output_has_agent_idle_prompt(output)
        || output_has_claude_turn_activity(output)
}

/// True while codex is still booting its MCP servers, e.g.
/// "Starting MCP servers (4/5): playwright (16h 25m • esc to interrupt)".
/// This boot phase reuses the active-work spinner, so it must NOT be
/// treated as the agent doing work.
fn output_is_starting_mcp_servers(output: &str) -> bool {
    output_tail_lower(output, 30).contains("starting mcp servers")
}

fn output_has_codex_active_marker(output: &str) -> bool {
    let text = output_tail_lower(output, 30);
    // MCP startup shows the same "(… • esc to interrupt)" spinner as
    // active work.  Counting it as active would set 'observed_active_at'
    // during boot and DISABLE the launch-timeout watchdog, letting a
    // hung MCP server pin the session at "running" indefinitely (the
    // 16h-stuck incident).  Boot is not work.
    if output_is_starting_mcp_servers(output) {
        return false;
    }
    // We only count markers that codex shows *during* active work.
    //
    // The status row ("Working (12s • esc to interrupt)") is the
    // reliable signal — both substrings only appear while the agent
    // is producing tokens.
    //
    // We deliberately do NOT match "ctrl + t to view transcript":
    // codex renders that hint next to collapsed historical turns
    // (e.g. "… +9 lines (ctrl + t to view transcript)") which stay
    // on screen indefinitely while the agent sits idle, so matching
    // it would make turn-end detection impossible on any pane that
    // has carried more than a couple of turns.
    text.contains("working (") || text.contains("esc to interrupt")
}

/// Active-work marker across supported agents.  Codex: see
/// `output_has_codex_active_marker`.  Claude Code: the working spinner
/// line, e.g. `✢ Swooping… (37s · ↓ 1.1k tokens · thinking…)` — the
/// `… (` sequence (ellipsis + space + open-paren) appears only on the
/// live spinner row; idle panes (input box, statusline, tips,
/// collapsed-turn hints) never render it.
fn output_has_agent_active_marker(output: &str) -> bool {
    if output_has_codex_active_marker(output) {
        return true;
    }
    output_has_live_claude_spinner_marker(output)
}

fn output_has_live_claude_spinner_marker(output: &str) -> bool {
    let lines: Vec<&str> = output.lines().collect();
    let Some(prompt_index) = lines.iter().rposition(|line| line.starts_with('❯')) else {
        return output_tail_lower(output, 30).contains("… (");
    };
    for line in lines[..prompt_index].iter().rev() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        return trimmed.contains("… (");
    }
    false
}

/// Evidence that Claude has actually consumed the injected prompt and
/// started producing a turn.  This is intentionally NOT an "active
/// marker": completed Claude turns leave `⏺` tool-call bullets and `⎿`
/// tool results on screen while idle, so using these as active-work
/// markers would suppress turn-end detection forever.  Use this only to
/// clear the just-launched awaiting-response latch and disarm the
/// startup watchdog.
fn output_has_claude_turn_activity(output: &str) -> bool {
    output.contains('⏺') || output.contains('⎿')
}

/// Detect codex's interactive "Update available!" version-check dialog,
/// e.g.
///
/// ```text
///   ✨ Update available! 0.134.0 -> 0.135.0
///   › 1. Update now (runs `npm install -g @openai/codex`)
///     2. Skip
///     3. Skip until next version
///   Press enter to continue
/// ```
///
/// Matches on the actionable MENU OPTIONS ("Update now" + "Skip until
/// next version") rather than the "Update available!" headline: the
/// headline can scroll out of the capture window above a long codex
/// banner, but the menu (next to "Press enter to continue") is on screen
/// exactly while the dialog is blocking input. Requiring both option
/// labels keeps ordinary agent output (which never contains both of
/// these exact phrases) from triggering a stray keystroke.
fn output_has_codex_update_dialog(output: &str) -> bool {
    let lower = output_tail_lower(output, 10);
    lower.contains("update now")
        && lower.contains("skip until next version")
        && lower.contains("press enter to continue")
}

fn codex_update_dialog_selected_option(output: &str) -> Option<u8> {
    for line in output.lines().rev().take(10) {
        let Some((_, after_marker)) = line.split_once('›') else {
            continue;
        };
        let selected = after_marker.trim_start().chars().next()?;
        match selected {
            '1' => return Some(1),
            '2' => return Some(2),
            '3' => return Some(3),
            _ => {}
        }
    }
    None
}

fn codex_update_dialog_down_steps_to_skip_until_next(output: &str) -> usize {
    let selected = codex_update_dialog_selected_option(output).unwrap_or(1);
    ((3 + 3 - selected as usize) % 3) as usize
}

/// True while Claude Code is blocking on the one-time
/// `--dangerously-skip-permissions` confirmation dialog.
///
/// The dialog renders its selected menu option with the same `❯`
/// glyph as Claude's input box, so it must be detected before the
/// generic idle-prompt check. Otherwise agentd sends the user prompt
/// into the menu and Enter accepts the default "No, exit" option.
fn output_has_claude_bypass_permissions_dialog(output: &str) -> bool {
    let lower = output.to_lowercase();
    lower.contains("bypass permissions mode")
        && lower.contains("no, exit")
        && lower.contains("yes, i accept")
        && lower.contains("enter to confirm")
}

/// True while Claude Code is blocking on the one-time fullscreen
/// renderer opt-in dialog.
///
/// Like the bypass dialog, this menu renders the selected option with
/// `❯`, so it must be cleared before treating `❯` as the REPL input
/// box.  agentd chooses "Not now" to keep automated terminal captures
/// on the stable renderer used by the existing heuristics.
fn output_has_claude_fullscreen_renderer_dialog(output: &str) -> bool {
    let lower = output.to_lowercase();
    lower.contains("try the new fullscreen renderer?")
        && lower.contains("yes, try it")
        && lower.contains("not now")
        && lower.contains("enter to confirm")
}

/// True while Claude Code is blocking on an organization managed-settings
/// approval dialog.  This can appear after a turn has already started, so
/// the monitor loop handles it in addition to launch-time readiness.
fn output_has_claude_managed_settings_approval_dialog(output: &str) -> bool {
    let lower = output.to_lowercase();
    lower.contains("managed settings require approval")
        && lower.contains("settings requiring approval")
}

/// Dismiss the codex update dialog by selecting "Skip until next version"
/// (the last option) so it does not re-prompt for the same release.
///
/// Codex has changed the initial highlight across versions: older builds
/// selected "1. Update now", while 0.142.x can select "2. Skip". Parse the
/// highlighted option and move to option 3 from there; fall back to the older
/// default only when no highlight marker is visible.
fn dismiss_codex_update_dialog(config: &Config, pane_id: &str, output: &str) -> Result<()> {
    for _ in 0..codex_update_dialog_down_steps_to_skip_until_next(output) {
        tmux_send_keys(config, pane_id, "Down", false, false)?;
        thread::sleep(Duration::from_millis(120));
    }
    tmux_send_keys(config, pane_id, "Enter", false, false)?;
    Ok(())
}

/// Accept Claude Code's bypass-permissions confirmation dialog.
///
/// The default selection is "1. No, exit"; move exactly once to
/// "2. Yes, I accept" before confirming. This is still terminal
/// transport, not prompt-in-argv: it handles the CLI's startup menu
/// before the actual user task is pasted into the live REPL.
fn accept_claude_bypass_permissions_dialog(config: &Config, pane_id: &str) -> Result<()> {
    tmux_send_keys(config, pane_id, "Down", false, false)?;
    thread::sleep(Duration::from_millis(120));
    tmux_send_keys(config, pane_id, "Enter", false, false)?;
    Ok(())
}

/// Dismiss Claude Code's fullscreen-renderer opt-in by selecting
/// "Not now". The default is "1. Yes, try it"; one Down then Enter
/// keeps the legacy renderer for predictable agentd capture parsing.
fn dismiss_claude_fullscreen_renderer_dialog(config: &Config, pane_id: &str) -> Result<()> {
    tmux_send_keys(config, pane_id, "Down", false, false)?;
    thread::sleep(Duration::from_millis(120));
    tmux_send_keys(config, pane_id, "Enter", false, false)?;
    Ok(())
}

/// Accept the organization managed-settings approval dialog.  The dialog
/// exists so the human-visible session acknowledges managed env/policy
/// settings; for agentd-launched real-agent tests and workers, the
/// operator has already chosen to run this account, so we accept and let
/// Claude continue the turn.
fn accept_claude_managed_settings_approval_dialog(config: &Config, pane_id: &str) -> Result<()> {
    tmux_send_keys(config, pane_id, "Enter", false, false)?;
    Ok(())
}

/// Verdict returned by the interactive-prompt judge
/// (ADR-DOE-AGENTS-002 R5).  The judge is the GENERAL path for unknown
/// blocking prompts; the hardcoded dialog detectors above remain as
/// deterministic fast-paths for the known ones (R9).
#[derive(Debug, Deserialize)]
struct PromptJudgeVerdict {
    blocked: bool,
    #[serde(default)]
    keys: Vec<String>,
    #[serde(default)]
    reason: String,
}

/// tmux send-keys names a judge verdict may use.  The whitelist bounds
/// the damage of a hallucinated verdict: navigation, confirmation and
/// single alphanumerics only — never control sequences.
fn is_allowed_unblock_key(key: &str) -> bool {
    let mut chars = key.chars();
    if let (Some(first), None) = (chars.next(), chars.next()) {
        return first.is_ascii_alphanumeric();
    }
    matches!(
        key,
        "Up" | "Down" | "Left" | "Right" | "Enter" | "Escape" | "Tab" | "Space" | "BSpace"
            | "Home" | "End"
    )
}

/// The full prompt handed to the judge command on stdin.  agentd owns
/// the instruction framing so the configured command stays a plain
/// "LLM with stdin/stdout" adapter.
fn prompt_judge_instructions(pane: &str) -> String {
    format!(
        "You are a terminal-UI judge inside an agent supervisor. Below is a tmux pane \
         capture of a coding-agent CLI session whose screen has stopped changing. Decide \
         whether the pane is BLOCKED on an interactive prompt (menu, confirmation dialog, \
         pager, login prompt) that is waiting for keyboard input. If it is, produce the \
         shortest safe key sequence that dismisses the prompt while PRESERVING current \
         behaviour — prefer options like 'Keep current model', 'Skip', 'Not now', 'No'. \
         A normal idle REPL prompt or ordinary scrolled output is NOT blocked.\n\
         Respond with ONLY one JSON object, no prose:\n\
         {{\"blocked\": true, \"keys\": [\"Down\", \"Enter\"], \"reason\": \"...\"}}\n\
         Allowed key names: single letters/digits, Up, Down, Left, Right, Enter, Escape, \
         Tab, Space, BSpace, Home, End.\n\
         PANE CAPTURE:\n{pane}"
    )
}

/// Run the judge command (`sh -c <cmd>`) with `stdin_text` on stdin and
/// a hard wall-clock cap.  The judge runs inside the monitor tick, so a
/// hung judge must not stall observation of the other sessions.
fn run_judge_command(cmd: &str, stdin_text: &str, timeout: Duration) -> Result<String> {
    use std::io::{Read, Write};
    use std::process::Stdio;
    let mut child = Command::new("sh")
        .args(["-c", cmd])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .context("prompt judge failed to spawn")?;
    // The judge prompt (instructions + a 100-line pane capture) is far
    // below the pipe buffer and the judge's JSON reply is tiny, so a
    // plain sequential write→poll→read cannot deadlock here.
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(stdin_text.as_bytes())
            .context("prompt judge stdin write failed")?;
    }
    let deadline = std::time::Instant::now() + timeout;
    loop {
        if child
            .try_wait()
            .context("prompt judge wait failed")?
            .is_some()
        {
            let mut out = String::new();
            if let Some(mut stdout) = child.stdout.take() {
                stdout
                    .read_to_string(&mut out)
                    .context("prompt judge stdout read failed")?;
            }
            return Ok(out);
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(anyhow!(
                "prompt judge timed out after {}s",
                timeout.as_secs()
            ));
        }
        thread::sleep(Duration::from_millis(100));
    }
}

/// Parse and validate a judge reply.  Anything other than one JSON
/// object with whitelisted keys is a judge failure — the callers decide
/// whether that degrades to solicitation (turn-end site) or fails the
/// session loudly (stall site), per ADR-DOE-AGENTS-002 R7.
fn parse_prompt_judge_verdict(raw: &str) -> Result<PromptJudgeVerdict> {
    let start = raw
        .find('{')
        .ok_or_else(|| anyhow!("prompt judge reply contains no JSON object: {raw:?}"))?;
    let end = raw
        .rfind('}')
        .ok_or_else(|| anyhow!("prompt judge reply contains no JSON object: {raw:?}"))?;
    if end < start {
        return Err(anyhow!("prompt judge reply contains no JSON object: {raw:?}"));
    }
    let verdict: PromptJudgeVerdict = serde_json::from_str(&raw[start..=end])
        .context("prompt judge reply is not valid verdict JSON")?;
    if verdict.blocked {
        if verdict.keys.is_empty() {
            return Err(anyhow!("prompt judge verdict is blocked but carries no keys"));
        }
        if verdict.keys.len() > PROMPT_JUDGE_MAX_KEYS {
            return Err(anyhow!(
                "prompt judge verdict carries {} keys (max {})",
                verdict.keys.len(),
                PROMPT_JUDGE_MAX_KEYS
            ));
        }
        if let Some(bad) = verdict.keys.iter().find(|k| !is_allowed_unblock_key(k)) {
            return Err(anyhow!("prompt judge verdict uses disallowed key {bad:?}"));
        }
    }
    Ok(verdict)
}

/// Ask the configured judge whether the captured pane is blocked on an
/// interactive prompt.  Errors cover: judge not configured, spawn/timeout
/// failures, and invalid verdicts.
fn judge_interactive_prompt(config: &Config, pane: &str) -> Result<PromptJudgeVerdict> {
    let cmd = config
        .prompt_judge_cmd
        .as_deref()
        .ok_or_else(|| anyhow!("no prompt judge configured"))?;
    let reply = run_judge_command(
        cmd,
        &prompt_judge_instructions(pane),
        Duration::from_secs(PROMPT_JUDGE_TIMEOUT_SECONDS),
    )?;
    parse_prompt_judge_verdict(&reply)
}

/// Send a validated unblock key sequence, mirroring the pacing of the
/// hardcoded dialog dismissers (120ms between keys).
fn send_unblock_keys(config: &Config, pane_id: &str, keys: &[String]) -> Result<()> {
    for key in keys {
        tmux_send_keys(config, pane_id, key, false, false)?;
        thread::sleep(Duration::from_millis(120));
    }
    Ok(())
}

/// Extract a human-readable message from a `catch_unwind` panic payload.
fn panic_payload_message(payload: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = payload.downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        String::from("<non-string panic payload>")
    }
}

/// Run one tick of a critical background worker with panic isolation.
///
/// A worker loop (`monitor_loop` / `heartbeat_loop`) runs forever on its
/// own thread.  Before this wrapper a *panic* inside the tick body
/// unwound straight out of the loop and killed the thread permanently —
/// with no supervision the worker never came back, yet the accept loop on
/// the main thread kept the daemon looking healthy.  The monitor is the
/// ONLY code that advances a session past `booting`, so a dead monitor
/// pins every session at `booting` (surfaced upstream as `AgentStarting`)
/// for the entire life of the process.  This is exactly what a disk-full
/// storm triggered: both workers panicked, and the panic messages were
/// lost because stderr (a log file on the full disk) could not be
/// written, so the death was completely silent.
///
/// Treat a panic the same way the loop already treats an `Err`: log it
/// and let the loop proceed to the next tick.  The tick body opens its
/// own sqlite connection per call, so unwinding drops that connection and
/// rolls back any in-flight transaction — there is no shared mutable
/// state left poisoned across ticks.  Once the transient condition (full
/// disk, locked db, flaky tmux) clears, the worker resumes on its own.
fn run_worker_tick<F>(worker: &str, tick: F)
where
    F: FnOnce() -> Result<()>,
{
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(tick)) {
        Ok(Ok(())) => {}
        Ok(Err(err)) => eprintln!("doeff-agentd {worker} error: {err:#}"),
        Err(payload) => eprintln!(
            "doeff-agentd {worker} PANIC recovered (worker continues): {}",
            panic_payload_message(payload.as_ref())
        ),
    }
}

fn monitor_loop(config: Config) {
    loop {
        run_worker_tick("monitor", || monitor_once(&config));
        thread::sleep(config.monitor_interval);
    }
}

fn heartbeat_loop(config: Config) {
    let interval = Duration::from_secs((LEASE_TTL_SECONDS as u64 / 3).max(1));
    loop {
        run_worker_tick("heartbeat", || heartbeat_once(&config));
        thread::sleep(interval);
    }
}

fn heartbeat_once(config: &Config) -> Result<()> {
    let conn = open_conn(&config.db_path)?;
    migrate(&conn)?;
    let current = read_lease(&conn)?
        .ok_or_else(|| anyhow!("doeff-agentd lease disappeared while daemon was running"))?;
    let owner_pid = i64::from(std::process::id());
    if current.owner_pid != owner_pid {
        return Err(anyhow!(
            "doeff-agentd lease owner changed: expected {} got {}",
            owner_pid,
            current.owner_pid
        ));
    }
    upsert_lease(&conn)
}

fn monitor_once(config: &Config) -> Result<()> {
    let conn = open_conn(&config.db_path)?;
    migrate(&conn)?;
    let active = session_list(
        &conn,
        ListParams {
            status: Some(active_statuses()),
            agent_type: None,
            backend_kind: Some(String::from("tmux")),
            lifecycle: None,
        },
    )?;
    let now = Utc::now();
    for mut snapshot in active {
        // Stale-observation watchdog. Runs BEFORE any tmux probe so a
        // hung or misbehaving tmux call cannot prevent the watchdog
        // from firing. Past incident: monitor_loop appeared live in
        // stack samples but DB writes stopped silently for ~11 hours,
        // pinning four sessions in `running` with no one driving them.
        // Touching only sqlite (with the global busy_timeout) ensures
        // this branch makes progress even if the rest of the pipeline
        // is broken.
        if let Some(last) = parse_iso_timestamp(snapshot.last_observed_at.as_deref()) {
            let age = now.signed_duration_since(last);
            let stale_threshold = effective_stale_observation_threshold_seconds();
            if age > ChronoDuration::seconds(stale_threshold) {
                snapshot.status = String::from("exited");
                let observed = now_iso();
                snapshot.last_observed_at = Some(observed.clone());
                snapshot.finished_at.get_or_insert(observed.clone());
                set_terminal_cause_if_absent(
                    &mut snapshot,
                    TerminalCauseCategory::Lost,
                    format!(
                        "no monitor observation for more than {}s",
                        stale_threshold
                    ),
                    true,
                    &observed,
                );
                upsert_snapshot(&conn, &snapshot)?;
                record_event(
                    &conn,
                    &snapshot.session_id,
                    "session_stale_reaped",
                    &snapshot,
                )?;
                continue;
            }
        }
        // Launch-timeout watchdog: a session that has been at status
        // `running` for longer than `LAUNCH_TIMEOUT_SECONDS` without
        // ever showing the agent's "active" marker is stuck inside
        // startup (typical cause: a hung MCP server holding codex's
        // initialisation loop indefinitely).  The stale-observation
        // watchdog above does not catch this because the startup
        // spinner ticks the wall-clock every second, so the tmux
        // capture keeps changing and `last_observed_at` keeps
        // refreshing.  Reap directly via SQL so a misbehaving tmux
        // child cannot itself block the watchdog.
        if snapshot.status == "running" && snapshot.observed_active_at.is_none() {
            if let Some(started) = parse_iso_timestamp(Some(snapshot.started_at.as_str())) {
                let age = now.signed_duration_since(started);
                let launch_timeout = effective_launch_timeout_seconds();
                if age > ChronoDuration::seconds(launch_timeout) {
                    snapshot.status = String::from("failed");
                    let observed = now_iso();
                    snapshot.last_observed_at = Some(observed.clone());
                    snapshot.finished_at.get_or_insert(observed.clone());
                    let reason = format!(
                        "launch timeout: never reached active state within {}s (stuck in startup — likely a hung MCP server)",
                        launch_timeout
                    );
                    snapshot.last_validation_error = Some(reason.clone());
                    set_terminal_cause_if_absent(
                        &mut snapshot,
                        TerminalCauseCategory::TimedOut,
                        reason,
                        true,
                        &observed,
                    );
                    upsert_snapshot(&conn, &snapshot)?;
                    record_event(
                        &conn,
                        &snapshot.session_id,
                        "session_launch_timeout",
                        &snapshot,
                    )?;
                    continue;
                }
            }
        }
        let exists = tmux_has_session(config, &snapshot.session_name)?;
        let observed_at = now_iso();
        if exists {
            // The launch RPC owns an initial BOOTING row until command/prompt
            // delivery completes.  The row is already externally observable,
            // but monitor-side pane I/O here would race the launch transport.
            if launch_transport_owns_snapshot(&snapshot) {
                continue;
            }
            // Zombie reaper: tmux session still exists but the
            // agent process inside the pane has exited and left
            // tmux at its parent shell.  Without this branch the
            // monitor sees a live tmux session, captures the
            // long-stale agent output, fails the turn-end stability
            // check forever (the output never changes), and the
            // session sits at @running@ holding a slot in the
            // concurrency cap.  Limit the check to sessions whose
            // status is already @running@ so we do not race against
            // the early-boot moment where the pane is briefly at
            // @zsh@ before the launcher's @send-keys@ actually
            // starts codex.
            if snapshot.status == "running" {
                if let Some(current_command) = tmux_pane_current_command(config, &snapshot.pane_id)?
                {
                    if pane_looks_like_idle_shell(&current_command) {
                        snapshot.status = String::from("exited");
                        snapshot.last_observed_at = Some(observed_at.clone());
                        snapshot.finished_at.get_or_insert_with(now_iso);
                        set_terminal_cause_if_absent(
                            &mut snapshot,
                            TerminalCauseCategory::Lost,
                            format!("tmux pane returned to idle shell: {current_command}"),
                            true,
                            &observed_at,
                        );
                        upsert_snapshot(&conn, &snapshot)?;
                        record_event(&conn, &snapshot.session_id, "session_exited", &snapshot)?;
                        continue;
                    }
                }
            }
            let output = tmux_capture(config, &snapshot.pane_id, 100)?;
            if snapshot.awaiting_response && output_has_unsubmitted_paste_input(&output, None) {
                tmux_send_enter(config, &snapshot.pane_id)?;
                snapshot.last_observed_at = Some(observed_at.clone());
                snapshot.output_snippet = Some(tail_chars(&output, 500));
                upsert_snapshot(&conn, &snapshot)?;
                record_event(
                    &conn,
                    &snapshot.session_id,
                    "session_unsubmitted_paste_resubmitted",
                    &snapshot,
                )?;
                thread::sleep(Duration::from_millis(800));
                continue;
            }
            if output_has_claude_managed_settings_approval_dialog(&output) {
                accept_claude_managed_settings_approval_dialog(config, &snapshot.pane_id)?;
                snapshot.last_observed_at = Some(observed_at.clone());
                snapshot.output_snippet = Some(tail_chars(&output, 500));
                if snapshot.observed_active_at.is_none() {
                    snapshot.observed_active_at = Some(observed_at.clone());
                }
                upsert_snapshot(&conn, &snapshot)?;
                record_event(&conn, &snapshot.session_id, "session_observed", &snapshot)?;
                thread::sleep(Duration::from_millis(800));
                continue;
            }
            // First, clear the awaiting-response latch once we see the
            // agent's "active" marker — that confirms the prompt landed
            // in the REPL and the agent is actually working on it.
            //
            // The marker must be POSITIVE work evidence (codex's status
            // row, claude's spinner line).  An earlier revision cleared
            // the latch on mere pane instability; that re-armed turn-end
            // inside the submit→spinner gap (a second or two of static
            // pane right after the prompt is sent), fired validation
            // before the agent had done anything, and burned the whole
            // retry budget on a healthy worker (observed live).
            let active_marker_seen = output_has_agent_active_marker(&output);
            let turn_activity_seen = output_has_claude_turn_activity(&output);
            if snapshot.awaiting_response && (active_marker_seen || turn_activity_seen) {
                snapshot.awaiting_response = false;
            }
            // Record the first time the agent visibly finished startup.
            // This is the signal the launch-timeout watchdog uses to
            // distinguish "agent finished startup" from "agent stuck
            // in startup": once set, the session is past the boot
            // phase and watchdog stops considering it for reaping.
            //
            // The codex active marker alone is NOT enough: claude never
            // shows it, so the watchdog reaped a healthy claude worker
            // mid-review as "stuck in startup" (observed live).  The
            // agent-agnostic startup-finished signal is the REPL input
            // box itself (`› ` / `❯`) — boot screens render it only once
            // initialisation is done.  MCP-boot and update-dialog panes
            // are explicitly excluded so the watchdog stays armed through
            // the hung-MCP startup it exists to catch.
            if snapshot.observed_active_at.is_none() && output_indicates_startup_finished(&output) {
                snapshot.observed_active_at = Some(observed_at.clone());
            }
            let raw_status = observed_status_for_snapshot(&snapshot, &output);
            snapshot.last_observed_at = Some(observed_at.clone());

            // Turn-end is the agent's "I finished one ply, what's next"
            // signal.  For a RunToCompletion contract session it means the
            // agent yielded without (yet) reporting a result; for an
            // Interactive session it means nothing (the session sits at the
            // idle prompt awaiting the next user input).
            //
            // 'awaiting_response' is the latch that ignores turn-end events
            // between sending the prompt and the agent visibly picking it up,
            // so we do not read a stale "Worked for" line from before the
            // prompt landed.
            //
            // CRITICAL: 'output_indicates_turn_end' compares the current
            // output against 'snapshot.output_snippet' to decide stability.
            // We must therefore evaluate it BEFORE writing the fresh snippet
            // back into the snapshot, otherwise the comparison degenerates
            // into "current == current" and every observation looks stable,
            // firing the turn-end branch prematurely.
            let output_changed = !output_is_stable(&snapshot, &output);
            let turn_ended =
                !snapshot.awaiting_response && output_indicates_turn_end(&snapshot, &output);
            snapshot.output_snippet = Some(tail_chars(&output, 500));
            // Stall clock for the interactive-prompt watchdog
            // (ADR-DOE-AGENTS-002 R5): last_output_change_at tracks content
            // CHANGE, unlike last_observed_at which refreshes on every
            // successful capture even when the pane is frozen.
            if output_changed || snapshot.last_output_change_at.is_none() {
                snapshot.last_output_change_at = Some(observed_at.clone());
            }

            let mut observed_status = raw_status;

            // ADR 0035: results arrive over the agentd-owned `report_result`
            // MCP data channel, never from the screen. `session_report_result`
            // persists a schema-valid payload into `result_payload_json`
            // (COALESCE-preserved so this loop's upserts cannot clobber it);
            // the monitor only OBSERVES that column and flips the session to
            // `done`.  The pane is never parsed for a result.
            if is_run_to_completion_lifecycle(&snapshot.lifecycle)
                && snapshot.expected_result.is_some()
            {
                // RESULT-FIRST: a reported result finalizes the session on any
                // cycle, regardless of turn-end.  Read it fresh so a result
                // reported since this tick's active-set query is seen at once
                // (report_result runs on a separate connection).
                if let Some(reported) = current_result_payload(&conn, &snapshot.session_id)? {
                    snapshot.result_payload = Some(reported);
                    snapshot.last_validation_error = None;
                    observed_status = "done";
                } else if turn_ended {
                    // The agent reached a stable turn-end without a valid
                    // reported result.  This is an OBSERVATION, not a
                    // deterministic failure (ADR-DOE-AGENTS-002 R1): the
                    // deterministic case is a schema-invalid payload, which
                    // session_report_result rejected with -32002 and never
                    // re-validates.  Re-read once more right before acting to
                    // close the (stability-gated, sub-tick) window against a
                    // result landing exactly at turn-end.
                    if let Some(reported) = current_result_payload(&conn, &snapshot.session_id)? {
                        snapshot.result_payload = Some(reported);
                        snapshot.last_validation_error = None;
                        observed_status = "done";
                    } else {
                        // Menu disambiguation (ADR-DOE-AGENTS-002 R6): codex
                        // menus render the same `› ` glyph as the idle REPL
                        // prompt, so this "turn-end" may actually be a
                        // blocking dialog eating input.  Pasting the
                        // solicitation there would press Enter on an
                        // arbitrary menu option — ask the judge first.  A
                        // judge failure here degrades to solicitation
                        // (bounded), never to a hang (R7).
                        if config.prompt_judge_cmd.is_some()
                            && snapshot.prompt_unblock_attempts < config.prompt_unblock_limit
                        {
                            snapshot.prompt_unblock_attempts += 1;
                            match judge_interactive_prompt(config, &output) {
                                Ok(verdict) if verdict.blocked => {
                                    send_unblock_keys(config, &snapshot.pane_id, &verdict.keys)?;
                                    upsert_snapshot(&conn, &snapshot)?;
                                    record_event(
                                        &conn,
                                        &snapshot.session_id,
                                        "session_prompt_unblocked",
                                        &snapshot,
                                    )?;
                                    continue;
                                }
                                Ok(_) => {}
                                Err(err) => {
                                    eprintln!(
                                        "doeff-agentd prompt judge (turn-end) failed for {}: {err:#}",
                                        snapshot.session_id
                                    );
                                }
                            }
                        }
                        if snapshot.result_solicitations_used < config.result_solicitation_limit {
                            // Bounded solicitation (R1/R2): tell the agent to
                            // call report_result and nothing else, re-arm the
                            // awaiting_response latch so turn-end stays quiet
                            // until the agent visibly picks the message up,
                            // and keep the session non-terminal so a landing
                            // report_result wins (R4).  The counter is a
                            // durable column, so the bound survives daemon
                            // restarts (the latch does not).
                            snapshot.result_solicitations_used += 1;
                            tmux_send_keys(
                                config,
                                &snapshot.pane_id,
                                RESULT_SOLICITATION_MESSAGE,
                                true,
                                true,
                            )?;
                            snapshot.awaiting_response = true;
                            upsert_snapshot(&conn, &snapshot)?;
                            record_event(
                                &conn,
                                &snapshot.session_id,
                                "session_result_solicited",
                                &snapshot,
                            )?;
                            continue;
                        }
                        observed_status = "failed";
                        let reason = if snapshot.result_solicitations_used == 0 {
                            String::from(
                                "session reached turn-end without reporting a result via report_result",
                            )
                        } else {
                            format!(
                                "session reached turn-end without reporting a result via report_result (after {} solicitation(s))",
                                snapshot.result_solicitations_used
                            )
                        };
                        snapshot.last_validation_error = Some(reason.clone());
                        set_terminal_cause_if_absent(
                            &mut snapshot,
                            TerminalCauseCategory::RunFailed,
                            reason,
                            false,
                            &observed_at,
                        );
                    }
                }
            } else if turn_ended && is_run_to_completion_lifecycle(&snapshot.lifecycle) {
                // RunToCompletion without an explicit contract: the launcher
                // trusts the turn-end signal as work-end.
                observed_status = "done";
            }

            // Interactive-prompt stall watchdog (ADR-DOE-AGENTS-002 R5/R7):
            // a pane that has stayed byte-identical past the stall threshold
            // with no active-work marker and no idle REPL prompt is blocked
            // on something turn-end detection can never see (login prompt,
            // pager, unknown dialog).  last_observed_at refreshes on every
            // capture, so no other watchdog fires for this state — before
            // this block such a session pinned a concurrency slot forever.
            // Bounded judge/unblock attempts, then a typed loud failure;
            // never an infinite wait.
            if observed_status == "running"
                && is_run_to_completion_lifecycle(&snapshot.lifecycle)
                && !snapshot.awaiting_response
                && snapshot.observed_active_at.is_some()
                && !active_marker_seen
                && !output_has_agent_idle_prompt(&output)
                && parse_iso_timestamp(snapshot.last_output_change_at.as_deref())
                    .map(|changed| {
                        now.signed_duration_since(changed)
                            > ChronoDuration::seconds(config.prompt_stall_seconds)
                    })
                    .unwrap_or(false)
            {
                let blocked_failure: Option<String> = if snapshot.prompt_unblock_attempts
                    >= config.prompt_unblock_limit
                {
                    Some(format!(
                        "interactive-prompt-blocked: pane unchanged for over {}s and {} unblock attempt(s) exhausted",
                        config.prompt_stall_seconds, snapshot.prompt_unblock_attempts
                    ))
                } else if config.prompt_judge_cmd.is_none() {
                    Some(format!(
                        "interactive-prompt-blocked: pane unchanged for over {}s and no prompt judge configured",
                        config.prompt_stall_seconds
                    ))
                } else {
                    snapshot.prompt_unblock_attempts += 1;
                    match judge_interactive_prompt(config, &output) {
                        Ok(verdict) if verdict.blocked => {
                            send_unblock_keys(config, &snapshot.pane_id, &verdict.keys)?;
                            upsert_snapshot(&conn, &snapshot)?;
                            record_event(
                                &conn,
                                &snapshot.session_id,
                                "session_prompt_unblocked",
                                &snapshot,
                            )?;
                            continue;
                        }
                        Ok(verdict) => {
                            // The judge sees no blocker, yet the pane has been
                            // frozen past the threshold with no work marker.
                            // Do not park forever on inconclusive verdicts:
                            // the attempt budget bounds these rounds too (R7).
                            eprintln!(
                                "doeff-agentd prompt judge saw no blocker for {} (attempt {}): {}",
                                snapshot.session_id,
                                snapshot.prompt_unblock_attempts,
                                verdict.reason
                            );
                            upsert_snapshot(&conn, &snapshot)?;
                            record_event(
                                &conn,
                                &snapshot.session_id,
                                "session_prompt_judge_inconclusive",
                                &snapshot,
                            )?;
                            continue;
                        }
                        // Judge unavailable / invalid verdict at the stall
                        // site: there is no other path that can unblock this
                        // pane, so fail loudly (R7) instead of waiting
                        // forever.
                        Err(err) => Some(format!(
                            "interactive-prompt-blocked: pane unchanged for over {}s and prompt judge failed: {err:#}",
                            config.prompt_stall_seconds
                        )),
                    }
                };
                if let Some(reason) = blocked_failure {
                    observed_status = "failed";
                    snapshot.last_validation_error = Some(reason.clone());
                    set_terminal_cause_if_absent(
                        &mut snapshot,
                        TerminalCauseCategory::InteractivePromptBlocked,
                        reason,
                        false,
                        &observed_at,
                    );
                }
            }

            snapshot.status = String::from(observed_status);
            if is_terminal_status(observed_status) {
                if observed_status == "failed" {
                    if let Some(reason) = snapshot.last_validation_error.clone() {
                        set_terminal_cause_if_absent(
                            &mut snapshot,
                            TerminalCauseCategory::RunFailed,
                            reason,
                            false,
                            &observed_at,
                        );
                    } else {
                        set_failed_output_cause_if_absent(&mut snapshot, &output, &observed_at);
                    }
                }
                snapshot.finished_at.get_or_insert_with(now_iso);
            }
            if should_cleanup_after_observed_status(&snapshot, observed_status)
                && tmux_has_session(config, &snapshot.session_name)?
            {
                tmux_kill_session(config, &snapshot.session_name)?;
                snapshot.cleaned_at.get_or_insert_with(now_iso);
            }
            upsert_snapshot(&conn, &snapshot)?;
            record_event(
                &conn,
                &snapshot.session_id,
                event_type_for_observed_status(observed_status),
                &snapshot,
            )?;
        } else {
            // The tmux session is gone. RESULT-FIRST: if the agent reported
            // a result over the data channel before its process exited, that
            // is a completed run, not a lost one (ADR 0035).
            let reported = if snapshot.expected_result.is_some() {
                current_result_payload(&conn, &snapshot.session_id)?
            } else {
                None
            };
            snapshot.last_observed_at = Some(observed_at.clone());
            snapshot.finished_at = Some(observed_at.clone());
            if let Some(reported) = reported {
                snapshot.result_payload = Some(reported);
                snapshot.last_validation_error = None;
                snapshot.status = String::from("done");
                upsert_snapshot(&conn, &snapshot)?;
                record_event(&conn, &snapshot.session_id, "session_done", &snapshot)?;
            } else {
                snapshot.status = String::from("exited");
                set_terminal_cause_if_absent(
                    &mut snapshot,
                    TerminalCauseCategory::Lost,
                    "tmux session disappeared",
                    true,
                    &observed_at,
                );
                upsert_snapshot(&conn, &snapshot)?;
                record_event(&conn, &snapshot.session_id, "session_exited", &snapshot)?;
            }
        }
    }
    Ok(())
}

/// Read a session's persisted, byte-faithful result payload (the JSON the
/// agent delivered over the `report_result` channel), if any.  Targeted
/// read used by the monitor's RESULT-FIRST checks: `report_result` writes
/// this column on a separate connection, so the monitor must re-read it
/// rather than trust its (possibly stale) active-set snapshot.
fn current_result_payload(conn: &Connection, session_id: &str) -> Result<Option<String>> {
    conn.query_row(
        "SELECT result_payload_json FROM agent_sessions WHERE session_id = ?1",
        params![session_id],
        |row| row.get::<_, Option<String>>(0),
    )
    .optional()
    .map(|opt| opt.flatten())
    .map_err(Into::into)
}

/// Validate `instance` against a constrained subset of JSON Schema.
///
/// Supported keywords: `type`, `const`, `minLength`, `pattern`, `required`,
/// `properties`, `oneOf`.  This is intentionally NOT a full JSON-Schema
/// implementation — it covers exactly what the launcher-supplied
/// contracts need (discriminated unions via `oneOf` + `const`, presence
/// via `required`, and non-empty strings via `minLength`) so the daemon
/// stays free of a heavyweight schema dependency and, more importantly,
/// can phrase violations as actionable feedback the agent can act on.
///
/// `loc` is a dotted breadcrumb (e.g. `payload.pr_url`) woven into the
/// error message so the agent knows which field to fix.
fn validate_against_schema(
    instance: &Value,
    schema: &Value,
    loc: &str,
) -> std::result::Result<(), String> {
    let obj = match schema.as_object() {
        Some(o) => o,
        None => return Err(format!("schema at '{loc}' is not a JSON object")),
    };

    // oneOf: exactly one branch must match.  Reported as an aggregate so
    // the agent sees why every variant was rejected.
    if let Some(one_of) = obj.get("oneOf") {
        let branches = one_of
            .as_array()
            .ok_or_else(|| format!("'oneOf' at '{loc}' must be an array"))?;
        let mut matched = 0usize;
        let mut branch_errors = Vec::new();
        for (i, branch) in branches.iter().enumerate() {
            match validate_against_schema(instance, branch, loc) {
                Ok(()) => matched += 1,
                Err(e) => branch_errors.push(format!("  variant {i}: {e}")),
            }
        }
        match matched {
            1 => {}
            0 => {
                return Err(format!(
                    "value at '{loc}' matched none of the {} allowed variants:\n{}",
                    branches.len(),
                    branch_errors.join("\n")
                ));
            }
            n => {
                return Err(format!(
                    "value at '{loc}' matched {n} variants but exactly one is allowed"
                ));
            }
        }
    }

    // const: exact value equality.
    if let Some(expected) = obj.get("const") {
        if instance != expected {
            return Err(format!("'{loc}' must equal {expected}"));
        }
    }

    // type: JSON type tag.
    if let Some(ty) = obj.get("type").and_then(|v| v.as_str()) {
        let ok = match ty {
            "object" => instance.is_object(),
            "array" => instance.is_array(),
            "string" => instance.is_string(),
            "number" => instance.is_number(),
            "integer" => instance.is_i64() || instance.is_u64(),
            "boolean" => instance.is_boolean(),
            "null" => instance.is_null(),
            other => {
                return Err(format!("schema at '{loc}' uses unsupported type '{other}'"));
            }
        };
        if !ok {
            return Err(format!("'{loc}' must be of type {ty}"));
        }
    }

    // minLength: non-empty / minimum-length strings.
    if let Some(min) = obj.get("minLength").and_then(|v| v.as_u64()) {
        if let Some(s) = instance.as_str() {
            if (s.chars().count() as u64) < min {
                return Err(format!(
                    "'{loc}' must be a string of at least length {min} (got {} chars)",
                    s.chars().count()
                ));
            }
        }
    }

    // pattern: used by result contracts for identity fields such as PR URLs,
    // SHAs, and branch names. Invalid schema patterns fail closed.
    if let Some(pattern) = obj.get("pattern").and_then(|v| v.as_str()) {
        if let Some(s) = instance.as_str() {
            let re = Regex::new(pattern)
                .map_err(|err| format!("schema at '{loc}' has invalid pattern {pattern:?}: {err}"))?;
            if !re.is_match(s) {
                return Err(format!("'{loc}' must match pattern {pattern:?}"));
            }
        }
    }

    // required: named fields must be present on an object.
    if let Some(req) = obj.get("required").and_then(|v| v.as_array()) {
        let map = instance.as_object();
        for key in req {
            if let Some(k) = key.as_str() {
                let present = map.map(|m| m.contains_key(k)).unwrap_or(false);
                if !present {
                    return Err(format!("'{loc}' is missing required field '{k}'"));
                }
            }
        }
    }

    // properties: recurse into present children only (absence is governed
    // by `required`, mirroring JSON-Schema semantics).
    if let Some(props) = obj.get("properties").and_then(|v| v.as_object()) {
        if let Some(map) = instance.as_object() {
            for (key, subschema) in props {
                if let Some(child) = map.get(key) {
                    validate_against_schema(child, subschema, &format!("{loc}.{key}"))?;
                }
            }
        }
    }

    Ok(())
}

// ADR 0035 removed the re-prompt path entirely. A deterministic
// validation failure (a schema-invalid or missing result) is never
// retried (hard rule 7): the session fails on first occurrence and the
// failure is surfaced to the caller. `report_result` gives the agent
// synchronous, same-turn feedback so it can self-correct within its own
// turn, but agentd itself never injects a retry prompt.

fn is_interactive_agent_type(agent_type: &str) -> bool {
    matches!(agent_type, "codex" | "claude")
}

/// Poll the tmux pane until the agent's REPL is in a state where it is
/// ready to receive a user message — concretely, the codex / claude
/// idle prompt marker (`›`) is visible.  Codex prints a banner and
/// loads MCP servers before its input loop is wired up; sending keys
/// during that window queues the text in the pty but lets the Enter
/// get eaten by the loading UI, leaving the prompt sitting in the
/// input box without ever being submitted.  Returns once the marker
/// appears or after `max_wait`, whichever comes first. Returns `false` on
/// timeout so the caller can terminalize the already-registered lifecycle row
/// without delivering the prompt to an unknown screen.
fn wait_for_repl_idle(config: &Config, pane_id: &str, max_wait: Duration) -> Result<bool> {
    let start = std::time::Instant::now();
    let poll_interval = Duration::from_millis(300);
    while start.elapsed() < max_wait {
        let output = tmux_capture(config, pane_id, 60)?;
        // Codex can interrupt startup with an interactive "Update
        // available!" version-check dialog whose DEFAULT highlight is
        // "1. Update now" — pressing Enter there runs
        // `npm install -g @openai/codex` and stalls the agent for the
        // whole upgrade (and may fail in a sandboxed workspace). The
        // dialog also renders the `›` selection marker, so
        // 'output_has_agent_idle_prompt' would mistake it for a ready
        // REPL and we'd send the prompt straight into the menu.
        // Detect and dismiss it BEFORE the idle check.
        if output_has_codex_update_dialog(&output) {
            dismiss_codex_update_dialog(config, pane_id, &output)?;
            // Give codex time to process the selection and redraw the
            // REPL before the next capture, so we don't re-detect a
            // half-cleared dialog and send another Down/Down/Enter into
            // what is by then the input box.
            thread::sleep(Duration::from_millis(800));
            continue;
        }
        if output_has_claude_bypass_permissions_dialog(&output) {
            accept_claude_bypass_permissions_dialog(config, pane_id)?;
            // Claude redraws from the confirmation screen into the
            // normal REPL; wait for that transition before checking for
            // the `❯` input box again.
            thread::sleep(Duration::from_millis(800));
            continue;
        }
        if output_has_claude_fullscreen_renderer_dialog(&output) {
            dismiss_claude_fullscreen_renderer_dialog(config, pane_id)?;
            // Let Claude redraw after the one-time renderer dialog
            // before checking for the normal input box.
            thread::sleep(Duration::from_millis(800));
            continue;
        }
        if output_has_claude_managed_settings_approval_dialog(&output) {
            accept_claude_managed_settings_approval_dialog(config, pane_id)?;
            thread::sleep(Duration::from_millis(800));
            continue;
        }
        if output_has_agent_idle_prompt(&output) {
            return Ok(true);
        }
        thread::sleep(poll_interval);
    }
    Ok(false)
}

fn tail_chars(value: &str, max_chars: usize) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= max_chars {
        return value.to_string();
    }
    chars[chars.len() - max_chars..].iter().collect()
}

#[allow(dead_code)]
fn parse_datetime(value: &str) -> Result<DateTime<Utc>> {
    Ok(DateTime::parse_from_rfc3339(value)?.with_timezone(&Utc))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::path::Path;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn run_worker_tick_isolates_panics_and_errors() {
        // A worker tick that panics must NOT unwind out of run_worker_tick;
        // if it did, the real worker loop's thread would die permanently and
        // the daemon would silently stop monitoring (the booting-freeze bug).
        // Silence the default panic hook so the deliberate panic below does
        // not spam the test output, then restore it.
        let previous_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));

        run_worker_tick("test", || panic!("boom"));
        run_worker_tick("test", || Err(anyhow!("transient failure")));

        std::panic::set_hook(previous_hook);

        // A tick that succeeds runs to completion and its side effect is
        // observed — proving run_worker_tick actually invokes the body.
        let ran = AtomicUsize::new(0);
        run_worker_tick("test", || {
            ran.fetch_add(1, Ordering::SeqCst);
            Ok(())
        });
        assert_eq!(ran.load(Ordering::SeqCst), 1);
        // Reaching here at all means neither the panic nor the Err propagated.
    }

    #[test]
    fn detects_codex_update_dialog() {
        let dialog = "\
  ✨ Update available! 0.134.0 -> 0.135.0\n\
\n\
  Release notes: https://github.com/openai/codex/releases/latest\n\
\n\
› 1. Update now (runs `npm install -g @openai/codex`)\n\
  2. Skip\n\
  3. Skip until next version\n\
\n\
  Press enter to continue\n";
        assert!(output_has_codex_update_dialog(dialog));
    }

    #[test]
    fn codex_update_dialog_down_steps_follow_current_selection() {
        let option_one = "\
› 1. Update now (runs `npm install -g @openai/codex`)\n\
  2. Skip\n\
  3. Skip until next version\n\
\n\
  Press enter to continue\n";
        let option_two = "\
  1. Update now (runs `npm install -g @openai/codex`)\n\
› 2. Skip\n\
  3. Skip until next version\n\
\n\
  Press enter to continue\n";
        let option_three = "\
  1. Update now (runs `npm install -g @openai/codex`)\n\
  2. Skip\n\
› 3. Skip until next version\n\
\n\
  Press enter to continue\n";

        assert_eq!(codex_update_dialog_selected_option(option_one), Some(1));
        assert_eq!(codex_update_dialog_selected_option(option_two), Some(2));
        assert_eq!(codex_update_dialog_selected_option(option_three), Some(3));
        assert_eq!(
            codex_update_dialog_down_steps_to_skip_until_next(option_one),
            2
        );
        assert_eq!(
            codex_update_dialog_down_steps_to_skip_until_next(option_two),
            1
        );
        assert_eq!(
            codex_update_dialog_down_steps_to_skip_until_next(option_three),
            0
        );
        assert_eq!(
            codex_update_dialog_down_steps_to_skip_until_next(
                "Update now\nSkip until next version\nPress enter to continue\n"
            ),
            2
        );
    }

    #[test]
    fn codex_update_dialog_handles_live_selected_skip() {
        let dialog = "\
codex --yolo -c 'model_reasoning_effort=\"xhigh\"' --model gpt-5.5\n\
\n\
  ✨\u{200a}Update available! 0.142.4 -> 0.142.5\n\
\n\
  Release notes: https://github.com/openai/codex/releases/latest\n\
\n\
  1. Update now (runs `npm install -g @openai/codex`)\n\
› 2. Skip\n\
  3. Skip until next version\n\
\n\
  Press enter to continue\n";

        assert!(output_has_codex_update_dialog(dialog));
        assert_eq!(codex_update_dialog_selected_option(dialog), Some(2));
        assert_eq!(codex_update_dialog_down_steps_to_skip_until_next(dialog), 1);
        assert!(!output_indicates_startup_finished(dialog));
    }

    #[test]
    fn update_dialog_detector_ignores_ordinary_output() {
        // The idle REPL prompt must not look like the update dialog.
        assert!(!output_has_codex_update_dialog("› \n"));
        // A passing mention of "update" must not trigger a keystroke
        // without the menu options present.
        assert!(!output_has_codex_update_dialog(
            "I checked for an update available in the changelog.\n"
        ));
        // The update dialog must not be mistaken for an idle prompt path
        // even though it contains the `›` marker.
        let dialog = "\
› 1. Update now
  3. Skip until next version

  Press enter to continue
";
        assert!(output_has_codex_update_dialog(dialog));
    }

    #[test]
    fn update_dialog_detector_ignores_stale_scrollback_after_repl_ready() {
        let output = "\
  ✨ Update available! 0.141.0 -> 0.142.0
  1. Update now
  2. Skip
› 3. Skip until next version
  Press enter to continue

╭──────────────────────────────────────────────────────╮
│ >_ OpenAI Codex                                      │
╰──────────────────────────────────────────────────────╯

  Tip: Try the Codex App.

› Summarize recent commits
";
        assert!(!output_has_codex_update_dialog(output));
        assert!(output_has_agent_idle_prompt(output));
    }

    #[test]
    fn detects_claude_bypass_permissions_dialog() {
        let dialog = "\
────────────────────────────────────────────────────────────────────────────────
  WARNING: Claude Code running in Bypass Permissions mode

  In Bypass Permissions mode, Claude Code will not ask for your approval
  before running potentially dangerous commands.

  ❯ 1. No, exit
    2. Yes, I accept

  Enter to confirm · Esc to cancel
";
        assert!(output_has_claude_bypass_permissions_dialog(dialog));
    }

    #[test]
    fn claude_bypass_dialog_detector_ignores_ordinary_output() {
        assert!(!output_has_claude_bypass_permissions_dialog("❯\u{00A0}"));
        assert!(!output_has_claude_bypass_permissions_dialog(
            "I accept that bypass permissions mode exists."
        ));
    }

    #[test]
    fn detects_claude_fullscreen_renderer_dialog() {
        let dialog = "\
────────────────────────────────────────────────────────────────────────────────
  Try the new fullscreen renderer?

  · Flicker-free output
  · Mouse support

  ❯ 1. Yes, try it
    2. Not now

  Enter to confirm · Esc to cancel
";
        assert!(output_has_claude_fullscreen_renderer_dialog(dialog));
    }

    #[test]
    fn claude_fullscreen_renderer_detector_ignores_ordinary_output() {
        assert!(!output_has_claude_fullscreen_renderer_dialog("❯\u{00A0}"));
        assert!(!output_has_claude_fullscreen_renderer_dialog(
            "I tried the new fullscreen renderer and selected text."
        ));
    }

    #[test]
    fn detects_claude_managed_settings_approval_dialog() {
        let dialog = "\
 Managed settings require approval

 Your organization has configured managed settings that could allow execution
 of arbitrary code or interception of your prompts and responses.

 Settings requiring approval:
   · OTEL_EXPORTER_OTLP_LOGS_ENDPOINT
";
        assert!(output_has_claude_managed_settings_approval_dialog(dialog));
    }

    #[test]
    fn claude_turn_activity_is_not_active_work() {
        let finished_tool_turn = "\
⏺ Write(notes.txt)
  ⎿  Wrote 1 lines to notes.txt

❯\u{00A0}
";
        assert!(output_has_claude_turn_activity(finished_tool_turn));
        assert!(!output_has_agent_active_marker(finished_tool_turn));
        assert!(output_indicates_startup_finished(finished_tool_turn));
    }

    #[test]
    fn active_marker_ignores_mcp_startup_spinner() {
        // The 16h-stuck incident: codex's MCP-startup line reuses the
        // "esc to interrupt" spinner. It must NOT count as active work,
        // else observed_active_at gets set during boot and the
        // launch-timeout watchdog never reaps a hung MCP startup.
        let booting = "• Starting MCP servers (4/5): playwright (16h 25m 27s • esc to interrupt)\n";
        assert!(output_is_starting_mcp_servers(booting));
        assert!(!output_has_codex_active_marker(booting));
    }

    #[test]
    fn active_marker_true_for_real_work() {
        // Genuine mid-turn work (no MCP-startup line) still counts.
        assert!(output_has_codex_active_marker(
            "Working (12s • esc to interrupt)\n"
        ));
        assert!(output_has_codex_active_marker(
            "foo\nbar (3s • esc to interrupt)\n"
        ));
        assert!(!output_is_starting_mcp_servers(
            "Working (12s • esc to interrupt)\n"
        ));
    }

    #[test]
    fn launch_timeout_default_is_60s() {
        // The MCP/launch-startup wait defaults to 60s (overridable via
        // the DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS env var at runtime).
        assert_eq!(LAUNCH_TIMEOUT_SECONDS, 60);
    }

    #[test]
    fn stale_observation_default_is_300s() {
        // The stale-observation watchdog defaults to 300s (overridable
        // via the DOEFF_AGENTD_STALE_OBSERVATION_SECS env var at runtime
        // — a conformance-suite testability knob, semantics unchanged).
        assert_eq!(STALE_OBSERVATION_THRESHOLD_SECONDS, 300);
    }

    #[test]
    fn list_query_filters_snapshot() {
        let snapshot = SessionSnapshot {
            session_id: String::from("s1"),
            session_name: String::from("s1"),
            pane_id: String::from("%1"),
            agent_type: String::from("codex"),
            work_dir: String::from("/tmp"),
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            status: String::from("running"),
            backend_kind: String::from("tmux"),
            backend_ref: BTreeMap::new(),
            started_at: now_iso(),
            last_observed_at: None,
            finished_at: None,
            cleaned_at: None,
            pr_url: None,
            output_snippet: None,
            terminal_cause: None,
            expected_result: None,
            retries_used: 0,
            last_validation_error: None,
            awaiting_response: false,
            observed_active_at: None,
            result_payload: None,
            result_solicitations_used: 0,
            prompt_unblock_attempts: 0,
            last_output_change_at: None,
        };
        assert!(list_query_matches(
            &snapshot,
            &ListParams {
                status: Some(vec![String::from("running")]),
                agent_type: Some(String::from("codex")),
                backend_kind: Some(String::from("tmux")),
                lifecycle: Some(String::from(LIFECYCLE_RUN_TO_COMPLETION)),
            },
        ));
        assert!(!list_query_matches(
            &snapshot,
            &ListParams {
                status: Some(vec![String::from("failed")]),
                agent_type: None,
                backend_kind: None,
                lifecycle: None,
            },
        ));
    }

    #[test]
    fn migration_creates_session_tables() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let count: i64 = conn
            .query_row(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table'
                 AND name IN (
                   'agent_sessions',
                   'agent_session_events',
                   'agent_session_commands',
                   'agent_daemon_lease'
                 )",
                [],
                |row| row.get(0),
            )
            .expect("table count");
        assert_eq!(count, 4);
    }

    #[test]
    fn terminal_cause_round_trips_through_store() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let mut snapshot = snapshot_for_lifecycle(LIFECYCLE_RUN_TO_COMPLETION, "failed");
        let observed_at = now_iso();
        set_terminal_cause_if_absent(
            &mut snapshot,
            TerminalCauseCategory::TimedOut,
            "launch timeout",
            true,
            &observed_at,
        );
        upsert_snapshot(&conn, &snapshot).expect("upsert snapshot");

        let loaded = session_get(&conn, &snapshot.session_id)
            .expect("session_get")
            .expect("snapshot exists");
        let cause = loaded.terminal_cause.expect("terminal cause");
        assert!(matches!(cause.category, TerminalCauseCategory::TimedOut));
        assert_eq!(cause.reason.as_deref(), Some("launch timeout"));
        assert!(cause.retryable);
        assert_eq!(cause.observed_at, observed_at);
    }

    #[test]
    fn parse_args_uses_xdg_style_default_paths() {
        let config = parse_args(vec![String::from("serve")]).expect("default paths");
        assert!(config.db_path.ends_with("doeff/agentd.sqlite"));
        let socket_name = config
            .socket_path
            .file_name()
            .expect("socket filename")
            .to_string_lossy();
        assert!(socket_name == "agentd.sock" || socket_name.starts_with("doeff-agentd-"));
    }

    #[test]
    fn parse_args_accepts_socket_and_db() {
        let config = parse_args(vec![
            String::from("--db"),
            String::from("/tmp/a.sqlite"),
            String::from("--socket"),
            String::from("/tmp/a.sock"),
            String::from("--monitor-interval-ms"),
            String::from("250"),
            String::from("--max-running"),
            String::from("3"),
            String::from("serve"),
        ])
        .expect("config");
        assert_eq!(config.db_path, Path::new("/tmp/a.sqlite"));
        assert_eq!(config.socket_path, Path::new("/tmp/a.sock"));
        assert_eq!(config.monitor_interval, Duration::from_millis(250));
        assert_eq!(config.max_running, 3);
    }

    // ADR 0035: capture-pane survives ONLY as an OBSERVATION transport
    // (active-marker / turn-end / dialog detection). It is no longer the
    // source of agent results — those arrive over the report_result data
    // channel (see session_report_result / current_result_payload), so no
    // result-recovery code path calls tmux_capture.
    #[test]
    fn tmux_capture_is_observation_transport_only() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let tmux_bin = tmp.path().join("fake-tmux");
        let args_file = tmp.path().join("args.txt");
        fs::write(
            &tmux_bin,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{}'\nprintf 'captured\\n'\n",
                args_file.display()
            ),
        )
        .expect("write fake tmux");
        let mut perms = fs::metadata(&tmux_bin).expect("metadata").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&tmux_bin, perms).expect("chmod fake tmux");
        let config = Config {
            db_path: tmp.path().join("agentd.sqlite"),
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin: tmux_bin.to_string_lossy().into_owned(),
            monitor_interval: Duration::from_millis(1000),
            max_running: 1,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };

        let output = tmux_capture(&config, "%1", 40).expect("capture");

        assert_eq!(output, "captured\n");
        let args = fs::read_to_string(args_file).expect("read args");
        assert!(args.lines().any(|arg| arg == "capture-pane"));
        assert!(args.lines().any(|arg| arg == "-J"));
    }

    #[test]
    fn acquire_lease_rejects_active_owner() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        upsert_lease(&conn).expect("insert lease");

        let err = acquire_lease(&conn).expect_err("active lease should reject");

        assert!(err.to_string().contains("doeff-agentd lease is active"));
    }

    #[test]
    fn prepare_socket_path_removes_stale_socket_file() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let socket_path = tmp.path().join("agentd.sock");
        fs::write(&socket_path, "stale").expect("write stale socket placeholder");

        prepare_socket_path(&socket_path).expect("remove stale socket placeholder");

        assert!(!socket_path.exists());
    }

    #[test]
    fn session_launch_rejects_when_max_running_reached() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        upsert_snapshot(
            &conn,
            &SessionSnapshot {
                session_id: String::from("existing"),
                session_name: String::from("existing"),
                pane_id: String::from("%1"),
                agent_type: String::from("codex"),
                work_dir: String::from("/tmp"),
                lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
                status: String::from("running"),
                backend_kind: String::from("tmux"),
                backend_ref: BTreeMap::new(),
                started_at: now_iso(),
                expected_result: None,
                retries_used: 0,
                last_validation_error: None,
                awaiting_response: false,
                observed_active_at: None,
                result_payload: None,
                result_solicitations_used: 0,
                prompt_unblock_attempts: 0,
                last_output_change_at: None,
                last_observed_at: None,
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
                terminal_cause: None,
            },
        )
        .expect("insert active session");
        let config = Config {
            db_path: db,
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin: String::from("tmux"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 1,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };
        let err = session_launch(
            &conn,
            &config,
            LaunchParams {
                session_id: String::from("new"),
                session_name: String::from("new"),
                agent_type: String::from("codex"),
                work_dir: String::from("/tmp"),
                command: Some(String::from("true")),
                prompt: None,
                model: None,
                effort: None,
                mcp_servers: BTreeMap::new(),
                skip_trust_setup: true,
                lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
                session_env: BTreeMap::new(),
                expected_result: None,
            },
        )
        .expect_err("max running should reject before tmux");
        assert!(err
            .to_string()
            .contains("max running agent sessions reached"));
    }

    #[test]
    fn monitor_once_reaps_session_with_stale_observation() {
        // Watchdog: a `running` session whose last_observed_at is older
        // than STALE_OBSERVATION_THRESHOLD_SECONDS must be forced to
        // `exited` even if tmux probes would otherwise leave it
        // running. Touches only sqlite — no tmux binary required.
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let stale_iso = (Utc::now()
            - ChronoDuration::seconds(STALE_OBSERVATION_THRESHOLD_SECONDS + 60))
        .to_rfc3339();
        upsert_snapshot(
            &conn,
            &SessionSnapshot {
                session_id: String::from("stale-running"),
                session_name: String::from("stale-running"),
                pane_id: String::from("%1"),
                agent_type: String::from("codex"),
                work_dir: String::from("/tmp"),
                lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
                status: String::from("running"),
                backend_kind: String::from("tmux"),
                backend_ref: BTreeMap::new(),
                started_at: stale_iso.clone(),
                expected_result: None,
                retries_used: 0,
                last_validation_error: None,
                awaiting_response: false,
                observed_active_at: None,
                result_payload: None,
                result_solicitations_used: 0,
                prompt_unblock_attempts: 0,
                last_output_change_at: None,
                last_observed_at: Some(stale_iso),
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
                terminal_cause: None,
            },
        )
        .expect("insert stale session");
        let config = Config {
            db_path: db.clone(),
            socket_path: tmp.path().join("agentd.sock"),
            // Point tmux at a binary that always fails — proves the
            // watchdog path does not invoke tmux at all on the stale
            // row. If we ever do invoke it the test fails loudly.
            tmux_bin: String::from("/nonexistent/tmux"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 10,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };

        monitor_once(&config).expect("monitor_once succeeds via watchdog");

        let row: (String, Option<String>) = Connection::open(&db)
            .expect("reopen sqlite")
            .query_row(
                "SELECT status, finished_at FROM agent_sessions WHERE session_id = ?1",
                params!["stale-running"],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .expect("session row");
        assert_eq!(row.0, "exited");
        assert!(row.1.is_some(), "finished_at must be stamped on reap");
        let event: String = Connection::open(&db)
            .expect("reopen sqlite")
            .query_row(
                "SELECT event_type FROM agent_session_events \
                 WHERE session_id = ?1 ORDER BY id DESC LIMIT 1",
                params!["stale-running"],
                |r| r.get(0),
            )
            .expect("event row");
        assert_eq!(event, "session_stale_reaped");
    }

    #[test]
    fn monitor_once_fails_session_stuck_in_startup_past_launch_timeout() {
        // Launch-timeout watchdog: a session whose start time is past
        // 'LAUNCH_TIMEOUT_SECONDS' AND that has never recorded an
        // 'observed_active_at' is stuck inside startup (typical
        // cause: a hung MCP server blocking codex's initialisation).
        // The stale-observation watchdog above does NOT catch this
        // case because the startup spinner ticks the wall-clock
        // every second so the tmux capture changes and
        // 'last_observed_at' keeps refreshing.  This test pins the
        // distinction by keeping 'last_observed_at' very recent
        // while 'started_at' is well past the timeout and
        // 'observed_active_at' is None.
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let started =
            (Utc::now() - ChronoDuration::seconds(LAUNCH_TIMEOUT_SECONDS + 120)).to_rfc3339();
        let fresh_observation = Utc::now().to_rfc3339();
        upsert_snapshot(
            &conn,
            &SessionSnapshot {
                session_id: String::from("startup-hang"),
                session_name: String::from("startup-hang"),
                pane_id: String::from("%1"),
                agent_type: String::from("codex"),
                work_dir: String::from("/tmp"),
                lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
                status: String::from("running"),
                backend_kind: String::from("tmux"),
                backend_ref: BTreeMap::new(),
                started_at: started,
                expected_result: None,
                retries_used: 0,
                last_validation_error: None,
                awaiting_response: false,
                last_observed_at: Some(fresh_observation),
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
                terminal_cause: None,
                observed_active_at: None,
                result_payload: None,
                result_solicitations_used: 0,
                prompt_unblock_attempts: 0,
                last_output_change_at: None,
            },
        )
        .expect("insert hung-startup session");
        let config = Config {
            db_path: db.clone(),
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin: String::from("/nonexistent/tmux"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 10,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };

        monitor_once(&config).expect("monitor_once via launch-timeout watchdog");

        let row: (String, Option<String>) = Connection::open(&db)
            .expect("reopen sqlite")
            .query_row(
                "SELECT status, last_validation_error FROM agent_sessions WHERE session_id = ?1",
                params!["startup-hang"],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .expect("session row");
        assert_eq!(row.0, "failed");
        assert!(
            row.1
                .as_deref()
                .map(|s| s.starts_with("launch timeout:"))
                .unwrap_or(false),
            "expected launch-timeout reason, got {:?}",
            row.1
        );
        let event: String = Connection::open(&db)
            .expect("reopen sqlite")
            .query_row(
                "SELECT event_type FROM agent_session_events \
                 WHERE session_id = ?1 ORDER BY id DESC LIMIT 1",
                params!["startup-hang"],
                |r| r.get(0),
            )
            .expect("event row");
        assert_eq!(event, "session_launch_timeout");
    }

    #[test]
    fn monitor_once_leaves_session_alone_once_active_marker_was_seen() {
        // Counterpart: a session that DID reach the active marker
        // before the timeout (= got past startup) must not be reaped
        // by the launch-timeout watchdog even if it has been
        // running for far longer than 'LAUNCH_TIMEOUT_SECONDS'.
        // Long-running agent work must not look like a startup hang.
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let started =
            (Utc::now() - ChronoDuration::seconds(LAUNCH_TIMEOUT_SECONDS * 2)).to_rfc3339();
        let active_at =
            (Utc::now() - ChronoDuration::seconds(LAUNCH_TIMEOUT_SECONDS * 2 - 30)).to_rfc3339();
        let fresh_observation = Utc::now().to_rfc3339();
        upsert_snapshot(
            &conn,
            &SessionSnapshot {
                session_id: String::from("long-running-but-active"),
                session_name: String::from("doeff-agentd-launch-test-absent"),
                pane_id: String::from("%1"),
                agent_type: String::from("codex"),
                work_dir: String::from("/tmp"),
                lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
                status: String::from("running"),
                backend_kind: String::from("tmux"),
                backend_ref: BTreeMap::new(),
                started_at: started,
                expected_result: None,
                retries_used: 0,
                last_validation_error: None,
                awaiting_response: false,
                last_observed_at: Some(fresh_observation),
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
                terminal_cause: None,
                observed_active_at: Some(active_at),
                result_payload: None,
                result_solicitations_used: 0,
                prompt_unblock_attempts: 0,
                last_output_change_at: None,
            },
        )
        .expect("insert long-running active session");
        let config = Config {
            db_path: db.clone(),
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin: String::from("tmux"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 10,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };

        monitor_once(&config).expect("monitor_once succeeds");

        let count: i64 = Connection::open(&db)
            .expect("reopen sqlite")
            .query_row(
                "SELECT COUNT(*) FROM agent_session_events \
                 WHERE session_id = ?1 AND event_type = 'session_launch_timeout'",
                params!["long-running-but-active"],
                |r| r.get(0),
            )
            .expect("count");
        assert_eq!(count, 0);
    }

    #[test]
    fn monitor_once_leaves_recently_observed_session_alone() {
        // Inverse of the watchdog test: a session whose last_observed_at
        // is fresh must not be reaped, even if everything else about it
        // looks identical. The fall-through path then exercises tmux,
        // which we let succeed (the session is absent ⇒ status=exited
        // via the existing "no tmux session" branch). The point of this
        // test is that the stale watchdog itself stays off.
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let fresh_iso = Utc::now().to_rfc3339();
        upsert_snapshot(
            &conn,
            &SessionSnapshot {
                session_id: String::from("fresh-running"),
                session_name: String::from("doeff-agentd-monitor-test-absent"),
                pane_id: String::from("%1"),
                agent_type: String::from("codex"),
                work_dir: String::from("/tmp"),
                lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
                status: String::from("running"),
                backend_kind: String::from("tmux"),
                backend_ref: BTreeMap::new(),
                started_at: fresh_iso.clone(),
                expected_result: None,
                retries_used: 0,
                last_validation_error: None,
                awaiting_response: false,
                observed_active_at: None,
                result_payload: None,
                result_solicitations_used: 0,
                prompt_unblock_attempts: 0,
                last_output_change_at: None,
                last_observed_at: Some(fresh_iso),
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
                terminal_cause: None,
            },
        )
        .expect("insert fresh session");
        let config = Config {
            db_path: db.clone(),
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin: String::from("tmux"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 10,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };

        monitor_once(&config).expect("monitor_once succeeds");

        // No `session_stale_reaped` event should have been recorded for
        // this row — the watchdog path must stay silent on fresh data.
        let reaped_count: i64 = Connection::open(&db)
            .expect("reopen sqlite")
            .query_row(
                "SELECT COUNT(*) FROM agent_session_events \
                 WHERE session_id = ?1 AND event_type = 'session_stale_reaped'",
                params!["fresh-running"],
                |r| r.get(0),
            )
            .expect("count rows");
        assert_eq!(reaped_count, 0);
    }

    #[test]
    fn turn_end_signal_does_not_mark_session_done_without_contract_check() {
        // Codex always shows "Worked for X" between turns, even when
        // the larger task is unfinished.  observed_status_for_snapshot
        // must keep the session "running" — the work-end decision
        // belongs to monitor_once's contract-validation pass.
        let snapshot = snapshot_for_lifecycle("run_to_completion", "running");
        let output = "task completed successfully\nworked for 1m 2s\n› ";

        let status = observed_status_for_snapshot(&snapshot, output);

        assert_eq!(status, "running");
        assert!(!should_cleanup_after_observed_status(&snapshot, status));
    }

    #[test]
    fn booting_shell_frame_becomes_running_after_launch_handoff() {
        let mut snapshot = snapshot_for_lifecycle("run_to_completion", "booting");
        snapshot.awaiting_response = true;

        assert_eq!(observed_status_for_snapshot(&snapshot, "$ "), "running");
    }

    #[test]
    fn launch_transport_ownership_ends_when_prompt_latch_is_armed() {
        let mut snapshot = snapshot_for_lifecycle("run_to_completion", "booting");
        snapshot.awaiting_response = false;
        assert!(launch_transport_owns_snapshot(&snapshot));

        snapshot.awaiting_response = true;
        assert!(!launch_transport_owns_snapshot(&snapshot));
    }

    #[test]
    fn interactive_output_remains_available_for_more_input() {
        // For Kind 1 sessions, turn-end never changes status either:
        // the session keeps serving prompts until the client cancels.
        let snapshot = snapshot_for_lifecycle("interactive", "running");
        let output = "task completed successfully\nworked for 1m 2s\n› ";

        let status = observed_status_for_snapshot(&snapshot, output);

        assert_eq!(status, "running");
        assert!(!should_cleanup_after_observed_status(&snapshot, status));
    }

    #[test]
    fn output_indicates_turn_end_requires_idle_prompt_and_stable_output() {
        // Turn-end needs the idle prompt visible, no active marker,
        // and an output that matches the previous observation (the
        // stability guard absorbs codex's banner / streaming state).
        let mut snapshot = snapshot_for_lifecycle("run_to_completion", "running");
        let stable_tail = "› ";
        snapshot.output_snippet = Some(stable_tail.to_string());
        assert!(output_indicates_turn_end(&snapshot, stable_tail));

        // The "Worked for" line is NOT required: a long-running
        // session whose status line has scrolled out of view is
        // still parked at the idle prompt.
        let scrolled_tail = "  feat/some-branch · ~/some/dir\n› ";
        snapshot.output_snippet = Some(scrolled_tail.to_string());
        assert!(output_indicates_turn_end(&snapshot, scrolled_tail));

        // Active marker present → agent is still working, not a
        // turn end yet.
        let working = "Working (10s • esc to interrupt)\n› ";
        snapshot.output_snippet = Some(working.to_string());
        assert!(!output_indicates_turn_end(&snapshot, working));

        // Pane unstable → wait one more cycle before deciding.
        let cycle_n = "› ";
        let cycle_n_plus_1 = "  doing stuff\n› ";
        snapshot.output_snippet = Some(cycle_n.to_string());
        assert!(!output_indicates_turn_end(&snapshot, cycle_n_plus_1));
    }

    #[test]
    fn output_indicates_turn_end_recognizes_claude_idle_pane() {
        // Claude Code's input box uses `❯ `, not codex's `› `.  A
        // monitor that only knows the codex prompt never fires turn-end
        // for claude sessions, so their result contracts are never
        // validated (observed live: a finished claude worker with a
        // valid structured result sat "blocked" until the launcher's
        // awaits exhausted).
        let mut snapshot = snapshot_for_lifecycle("run_to_completion", "running");
        let claude_idle = "  done. result written.\n\n❯\u{00A0}\n  🏢 ca  Fable 5 XHI";
        snapshot.output_snippet = Some(claude_idle.to_string());
        assert!(output_indicates_turn_end(&snapshot, claude_idle));

        // While claude is WORKING the `❯` input box is still visible
        // below the spinner, so idle-prompt detection alone would
        // misfire; the per-second spinner tick keeps the pane unstable,
        // and the stability guard must hold the turn-end back.
        let working_t1 = "✢ Swooping… (37s · ↓ 1.1k tokens)\n\n❯\u{00A0}";
        let working_t2 = "✢ Swooping… (38s · ↓ 1.2k tokens)\n\n❯\u{00A0}";
        snapshot.output_snippet = Some(working_t1.to_string());
        assert!(!output_indicates_turn_end(&snapshot, working_t2));
    }

    #[test]
    fn agent_active_marker_recognizes_claude_spinner() {
        // Live claude working pane: spinner verb + `… (` + tick timer.
        assert!(output_has_agent_active_marker(
            "✢ Swooping… (37s · ↓ 1.1k tokens · thinking)\n\n❯\u{00A0}"
        ));
        // During the submit→work transition the input box can disappear
        // while the spinner is visible; this still proves the prompt was
        // accepted and clears the awaiting-response latch.
        assert!(output_has_agent_active_marker(
            "✢ Swooping… (1s · thinking)\n\n\n"
        ));
        // Idle claude pane: input box + statusline, no spinner row.
        assert!(!output_has_agent_active_marker(
            "  done.\n\n❯\u{00A0}\n  🏢 ca  Fable 5 XHI\n  $9.55 │ ⏱ 21m44s(api 10m30s)"
        ));
        // Historical spinner rows can remain in the transcript after
        // the turn produced output and returned to the input box; only
        // the row immediately above the input box is the live spinner.
        assert!(!output_has_agent_active_marker(
            "✢ Swooping… (1s · thinking)\n\
             wrote invalid result\n\n\
             ❯\u{00A0}"
        ));
        // Codex collapsed-turn hint must not read as active (the `… +9
        // lines (` form has text between the ellipsis and the paren).
        assert!(!output_has_agent_active_marker(
            "… +9 lines (ctrl + t to view transcript)\n› "
        ));
        // Codex working row still counts.
        assert!(output_has_agent_active_marker(
            "Working (12s • esc to interrupt)"
        ));
    }

    #[test]
    fn startup_finished_signal_is_agent_agnostic_but_boot_safe() {
        // The watchdog disarms once the REPL input box is visible —
        // codex `› ` or claude `❯` — or codex shows its work marker.
        assert!(output_indicates_startup_finished("banner\n❯\u{00A0}"));
        assert!(output_indicates_startup_finished("banner\n› "));
        assert!(output_indicates_startup_finished(
            "Working (10s • esc to interrupt)\n› "
        ));
        // Still booting (no input box yet): watchdog stays armed.
        assert!(!output_indicates_startup_finished("Loading…"));
        // Hung MCP startup is the case the watchdog exists for — the
        // spinner and even a visible prompt must not disarm it.
        assert!(!output_indicates_startup_finished(
            "Starting MCP servers (4/5): playwright (16h 25m • esc to interrupt)\n› "
        ));
        // The update dialog renders a `›` selection marker that must
        // not read as a ready REPL.
        assert!(!output_indicates_startup_finished(
            "✨ Update available!\n› 1. Update now\n  3. Skip until next version\nPress enter to continue"
        ));
        // Claude's bypass-permissions menu uses the same `❯` glyph as
        // the normal input box, but it is still a startup dialog.
        assert!(!output_indicates_startup_finished(
            "WARNING: Claude Code running in Bypass Permissions mode\n\
             ❯ 1. No, exit\n\
               2. Yes, I accept\n\
             Enter to confirm · Esc to cancel"
        ));
        assert!(!output_indicates_startup_finished(
            "Try the new fullscreen renderer?\n\
             ❯ 1. Yes, try it\n\
               2. Not now\n\
             Enter to confirm · Esc to cancel"
        ));
    }

    #[test]
    fn agent_idle_prompt_accepts_codex_and_claude_prompts() {
        assert!(output_has_agent_idle_prompt("› "));
        assert!(output_has_agent_idle_prompt("some scroll\n› "));
        // Real claude capture: the glyph is followed by U+00A0, not an
        // ASCII space, and may carry typed-but-unsubmitted text.
        assert!(output_has_agent_idle_prompt("❯\u{00A0}monitor-nudge"));
        assert!(output_has_agent_idle_prompt(
            "scroll\n❯\u{00A0}\n  statusline"
        ));
        assert!(output_has_agent_idle_prompt("❯"));
        assert!(!output_has_agent_idle_prompt("plain shell $ "));
        assert!(!output_has_agent_idle_prompt("  quoted mid-line ❯ glyph"));
    }

    #[test]
    fn detects_unsubmitted_collapsed_paste_input() {
        let stuck_claude_input = "\
ude Max
  ▘▘ ▝▝    /tmp/sbi-executor-sbi-company_2026-06-23

────────────────────────────────────────────────────────────────────────────────
❯\u{00A0}[Pasted text #1 +57 lines][Pasted text #2 +22 lines]
────────────────────────────────────────────────────────────────────────────────
  paste again to expand
";
        assert!(output_has_unsubmitted_paste_input(stuck_claude_input, None));

        let stuck_queued_claude_input = "\
────────────────────────────────────────────────────────────────────────────────
❯\u{00A0}Press up to edit queued messages
────────────────────────────────────────────────────────────────────────────────
   ⚠⚠ NOT FABLE — model: Opus 4.8 (claude-opus-4-8) ⚠⚠
";
        assert!(output_has_unsubmitted_paste_input(
            stuck_queued_claude_input,
            None
        ));

        let stuck_codex_input = "\
╭──────────────────────────────────────────────────────╮
│ >_ OpenAI Codex                                      │
╰──────────────────────────────────────────────────────╯

› [Pasted text #1 +12 lines]
";
        assert!(output_has_unsubmitted_paste_input(stuck_codex_input, None));

        let stuck_codex_content_input = "\
╭──────────────────────────────────────────────────────╮
│ >_ OpenAI Codex                                      │
╰──────────────────────────────────────────────────────╯

› Fix [Pasted Content 6532 chars]
";
        assert!(output_has_unsubmitted_paste_input(
            stuck_codex_content_input,
            None
        ));
    }

    #[test]
    fn unsubmitted_paste_detector_ignores_history_and_empty_prompt() {
        let historical_paste = "\
❯\u{00A0}[Pasted text #1 +2 lines]
⏺ Write(notes.txt)
  ⎿  Wrote 1 lines to notes.txt

❯\u{00A0}
";
        assert!(!output_has_unsubmitted_paste_input(historical_paste, None));
        assert!(!output_has_unsubmitted_paste_input("❯\u{00A0}\n", None));
        assert!(!output_has_unsubmitted_paste_input("› \n", None));
    }

    #[test]
    fn unsubmitted_paste_detector_catches_visible_prompt_text() {
        let output = "\
────────────────────────────────────────────────────────────────
❯\u{00A0}
────────────────────────────────────────────────────────────────

  Continue autonomously if safe, or return a blocked/error structured result.
";
        let sent_text = "\
The executor appeared to be waiting for input. Continue autonomously if safe, \
or return a blocked/error structured result.";

        assert!(output_has_unsubmitted_paste_input(output, Some(sent_text)));
    }

    #[test]
    fn unsubmitted_paste_detector_catches_codex_wrapped_prompt_text() {
        let sent_text = "\
Read .acp-context.json, compare the inbox issue against existing open issues, \
and write the structured triage result when done.";
        let output = "\
╭──────────────────────────────────────────────────────╮
│ >_ OpenAI Codex                                      │
╰──────────────────────────────────────────────────────╯

› Read .acp-context.json, compare the inbox issue
  against existing open issues, and write the structured
  triage result when done.
";

        assert!(output_has_unsubmitted_paste_input(output, Some(sent_text)));
    }

    #[test]
    fn unsubmitted_paste_detector_catches_non_ascii_prompt_tail() {
        let sent_text = "\
トリアージ担当です。状況は .acp-context.json にあります。payload.inbox_issues を比較し、\
構造化された結果を返してください。";
        let output = "\
╭──────────────────────────────────────────────────────╮
│ >_ OpenAI Codex                                      │
╰──────────────────────────────────────────────────────╯

› トリアージ担当です。状況は .acp-context.json にあります。
  payload.inbox_issues を比較し、構造化された結果を返してください。
";

        assert!(output_has_unsubmitted_paste_input(output, Some(sent_text)));
    }

    #[test]
    fn unsubmitted_paste_detector_ignores_prior_submitted_prompt_text() {
        let sent_text = "\
The executor appeared to be waiting for input. Continue autonomously if safe, \
or return a blocked/error structured result.";
        let output = "\
The executor appeared to be waiting for input. Continue autonomously if safe, \
or return a blocked/error structured result.
⏺ Running tools

❯\u{00A0}
";

        assert!(!output_has_unsubmitted_paste_input(output, Some(sent_text)));
    }

    #[test]
    fn unsubmitted_paste_detector_ignores_prior_codex_prompt_text() {
        let sent_text = "\
Read .acp-context.json, compare the inbox issue against existing open issues, \
and write the structured triage result when done.";
        let output = "\
› Read .acp-context.json, compare the inbox issue against existing open issues, \
and write the structured triage result when done.
Working...
›
";

        assert!(!output_has_unsubmitted_paste_input(output, Some(sent_text)));
    }

    /// A spec carrying just a schema, as the launcher sends it: agentd
    /// owns the result channel; the launcher supplies only `payload_schema`.
    fn schema_only_spec(schema: Value) -> ExpectedResultSpec {
        ExpectedResultSpec {
            payload_schema: schema,
        }
    }

    #[test]
    fn result_protocol_instruction_directs_agent_to_report_result_tool() {
        // ADR 0035: the instruction tells the agent to call the
        // report_result MCP tool, NOT to print a screen block. The old
        // DOEFF_AGENT_RESULT_BEGIN/END markers must not appear.
        let prompt = result_protocol_instruction("session-1");
        assert!(
            prompt.contains(REPORT_RESULT_TOOL),
            "instruction must name the report_result tool: {prompt}"
        );
        assert!(
            prompt.contains("payload"),
            "instruction must name the payload argument: {prompt}"
        );
        assert!(
            !prompt.contains("DOEFF_AGENT_RESULT_BEGIN")
                && !prompt.contains("DOEFF_AGENT_RESULT_END"),
            "instruction must not tell the agent to print a transcript block: {prompt}"
        );
    }

    // ---- validate_against_schema: the JSON-Schema subset ----

    /// The discriminated-union schema the impl launcher attaches: either
    /// a succeeded result with non-empty PR identity, or a blocked result
    /// that explains why no PR was produced.
    fn impl_result_payload_schema() -> Value {
        serde_json::json!({
            "oneOf": [
                {
                    "type": "object",
                    "required": ["pr_url", "pr_head_sha", "branch"],
                    "properties": {
                        "status": {"const": "succeeded"},
                        "pr_url": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": "^https://github\\.com/[^\\s/]+/[^\\s/]+/pull/[0-9]+$"
                        },
                        "pr_head_sha": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": "^[0-9a-fA-F]{40}$"
                        },
                        "branch": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": "^\\S+$"
                        }
                    }
                },
                {
                    "type": "object",
                    "required": ["status", "reason"],
                    "properties": {
                        "status": {"const": "blocked"},
                        "reason": {"type": "string", "minLength": 1}
                    }
                }
            ]
        })
    }

    #[test]
    fn schema_accepts_succeeded_payload_without_status() {
        // Backward compatibility: a legacy agent emits no `status` and
        // still matches the succeeded branch.
        let payload = serde_json::json!({
            "pr_url": "https://github.com/o/r/pull/1",
            "pr_head_sha": "0123456789abcdef0123456789abcdef01234567",
            "branch": "feat/x"
        });
        assert!(
            validate_against_schema(&payload, &impl_result_payload_schema(), "payload").is_ok()
        );
    }

    #[test]
    fn schema_accepts_blocked_payload() {
        let payload = serde_json::json!({
            "status": "blocked",
            "reason": "workspace allocation failed; no PR was produced"
        });
        assert!(
            validate_against_schema(&payload, &impl_result_payload_schema(), "payload").is_ok()
        );
    }

    #[test]
    fn schema_rejects_blank_pr_identity_masquerading_as_success() {
        // The exact bug this feature exists to catch: a blocked agent
        // wrote a "success" payload with empty identity strings.
        let payload = serde_json::json!({
            "pr_url": "",
            "pr_head_sha": "",
            "branch": ""
        });
        let err = validate_against_schema(&payload, &impl_result_payload_schema(), "payload")
            .expect_err("blank identity must be rejected");
        assert!(err.contains("matched none"), "got: {err}");
        // The aggregate must mention the minLength failure so the agent
        // knows the field is empty rather than absent.
        assert!(err.contains("length"), "got: {err}");
    }

    #[test]
    fn schema_rejects_wrapped_identity_fields_with_embedded_spaces() {
        let payload = serde_json::json!({
            "pr_url": "https://github.com/o/r/ pull/1",
            "pr_head_sha": "0123456789abcdef0123456789abcdef01234567",
            "branch": "feat/long-branch"
        });
        let err = validate_against_schema(&payload, &impl_result_payload_schema(), "payload")
            .expect_err("wrapped identity fields must reject");
        assert!(err.contains("payload.pr_url"), "got: {err}");
        assert!(err.contains("pattern"), "got: {err}");
    }

    #[test]
    fn schema_rejects_blocked_without_reason() {
        let payload = serde_json::json!({"status": "blocked"});
        let err = validate_against_schema(&payload, &impl_result_payload_schema(), "payload")
            .expect_err("blocked without reason must be rejected");
        assert!(err.contains("matched none"), "got: {err}");
    }

    #[test]
    fn schema_const_and_type_and_required_report_field_path() {
        let schema = serde_json::json!({
            "type": "object",
            "required": ["k"],
            "properties": {"k": {"type": "string", "minLength": 2}}
        });
        let missing = validate_against_schema(&serde_json::json!({}), &schema, "payload")
            .expect_err("missing required");
        assert!(missing.contains("required field 'k'"), "got: {missing}");
        let wrong_type = validate_against_schema(&serde_json::json!({"k": 5}), &schema, "payload")
            .expect_err("wrong type");
        assert!(wrong_type.contains("payload.k"), "got: {wrong_type}");
        assert!(wrong_type.contains("type string"), "got: {wrong_type}");
    }

    #[test]
    fn interactive_agent_types_are_codex_and_claude() {
        assert!(is_interactive_agent_type("codex"));
        assert!(is_interactive_agent_type("claude"));
        assert!(!is_interactive_agent_type("generic"));
        assert!(!is_interactive_agent_type(""));
    }

    #[test]
    fn session_env_injects_prompt_suppressors_and_lets_caller_override() {
        // A launch with no caller env still gets the baseline prompt-suppressors
        // so an interactive shell-startup prompt (e.g. oh-my-zsh's update [Y/n])
        // can never derail the agent we drive via send-keys.
        let entries = session_env_entries(&BTreeMap::new());
        assert!(entries.contains(&(String::from("DISABLE_AUTO_UPDATE"), String::from("true"))));
        assert!(entries.contains(&(String::from("DISABLE_UPDATE_PROMPT"), String::from("true"))));

        // A caller key passes through alongside the baseline.
        let mut caller = BTreeMap::new();
        caller.insert(String::from("CODEX_HOME"), String::from("/x/agent"));
        let entries = session_env_entries(&caller);
        assert!(entries.contains(&(String::from("CODEX_HOME"), String::from("/x/agent"))));
        assert!(entries.contains(&(String::from("DISABLE_AUTO_UPDATE"), String::from("true"))));

        // A caller override of a baseline key wins (baseline value not duplicated).
        let mut override_env = BTreeMap::new();
        override_env.insert(String::from("DISABLE_AUTO_UPDATE"), String::from("false"));
        let entries = session_env_entries(&override_env);
        let auto_update: Vec<_> = entries
            .iter()
            .filter(|(k, _)| k == "DISABLE_AUTO_UPDATE")
            .collect();
        assert_eq!(
            auto_update,
            vec![&(String::from("DISABLE_AUTO_UPDATE"), String::from("false"))]
        );
    }

    #[test]
    fn forbidden_agent_env_keys_detects_anthropic_api_key_aliases() {
        let mut env = BTreeMap::new();
        env.insert(String::from("ANTHROPIC_API_KEY"), String::from("secret"));
        env.insert(
            String::from("anthropic_api_key__personal"),
            String::from("secret"),
        );
        env.insert(
            String::from("anthropic-api-key-personal"),
            String::from("secret"),
        );
        env.insert(String::from("CODEX_HOME"), String::from("/tmp/codex"));

        let forbidden = forbidden_agent_env_keys(&env);

        assert_eq!(
            forbidden,
            vec![
                String::from("ANTHROPIC_API_KEY"),
                String::from("anthropic-api-key-personal"),
                String::from("anthropic_api_key__personal"),
            ]
        );
        assert!(ensure_no_forbidden_agent_env(&env).is_err());
    }

    fn launch_params_for(agent_type: &str, effort: Option<&str>) -> LaunchParams {
        LaunchParams {
            session_id: String::from("s"),
            session_name: String::from("s"),
            agent_type: String::from(agent_type),
            work_dir: String::from("/tmp"),
            command: None,
            prompt: Some(String::from("hello world")),
            model: None,
            effort: effort.map(String::from),
            mcp_servers: BTreeMap::new(),
            skip_trust_setup: true,
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            session_env: BTreeMap::new(),
            expected_result: None,
        }
    }

    #[test]
    fn build_codex_argv_does_not_include_prompt_as_argument() {
        let params = launch_params_for("codex", None);
        let argv = build_codex_argv(&params, None);
        assert!(
            !argv.iter().any(|arg| arg == "hello world"),
            "prompt must not appear in codex argv (it is sent via send-keys instead): {argv:?}"
        );
        assert_eq!(argv[0], "codex");
        assert!(argv.contains(&String::from("--yolo")));
    }

    #[test]
    fn build_codex_argv_delivers_effort_as_model_reasoning_effort() {
        let params = launch_params_for("codex", Some("xhigh"));
        let argv = build_codex_argv(&params, None);
        let position = argv
            .iter()
            .position(|arg| arg == "model_reasoning_effort=\"xhigh\"")
            .expect("effort config must appear in codex argv");
        assert_eq!(argv[position - 1], "-c");
    }

    #[test]
    fn build_codex_argv_omits_effort_when_absent_or_empty() {
        for effort in [None, Some("")] {
            let params = launch_params_for("codex", effort);
            let argv = build_codex_argv(&params, None);
            assert!(
                !argv
                    .iter()
                    .any(|arg| arg.contains("model_reasoning_effort")),
                "no effort config expected for effort={effort:?}: {argv:?}"
            );
        }
    }

    #[test]
    fn build_claude_argv_delivers_effort_flag() {
        let params = launch_params_for("claude", Some("xhigh"));
        let argv = build_claude_argv(&params, None);
        let position = argv
            .iter()
            .position(|arg| arg == "--effort")
            .expect("--effort must appear in claude argv");
        assert_eq!(argv[position + 1], "xhigh");
    }

    #[test]
    fn build_claude_argv_does_not_include_prompt_or_print_mode() {
        let params = launch_params_for("claude", None);
        let argv = build_claude_argv(&params, None);
        assert!(
            !argv.iter().any(|arg| arg == "hello world"),
            "prompt must not appear in claude argv: {argv:?}"
        );
        assert!(
            !argv.iter().any(|arg| arg == "-p" || arg == "--print"),
            "claude print mode must not appear in argv: {argv:?}"
        );
    }

    #[test]
    fn build_claude_argv_omits_effort_when_absent_or_empty() {
        for effort in [None, Some("")] {
            let params = launch_params_for("claude", effort);
            let argv = build_claude_argv(&params, None);
            assert!(
                !argv.iter().any(|arg| arg == "--effort"),
                "no --effort expected for effort={effort:?}: {argv:?}"
            );
        }
    }

    fn test_result_channel() -> ResultChannel {
        ResultChannel {
            command: String::from("/opt/doeff-agentd"),
            session_id: String::from("sess-123"),
            socket: String::from("/run/agentd.sock"),
        }
    }

    #[test]
    fn build_codex_argv_wires_report_result_stdio_mcp_server() {
        // ADR 0035: a contract session's codex launch carries the
        // agentd-owned report_result stdio MCP server (command + args), so
        // the agent can deliver its result over a byte-faithful channel.
        let params = launch_params_for("codex", None);
        let channel = test_result_channel();
        let argv = build_codex_argv(&params, Some(&channel));
        assert!(
            argv.iter().any(|a| a
                == &format!(
                    "mcp_servers.\"{REPORT_RESULT_MCP_SERVER}\".command=\"/opt/doeff-agentd\""
                )),
            "codex argv must set the report_result server command: {argv:?}"
        );
        let args_line = argv
            .iter()
            .find(|a| a.starts_with(&format!("mcp_servers.\"{REPORT_RESULT_MCP_SERVER}\".args=")))
            .unwrap_or_else(|| panic!("codex argv must set report_result server args: {argv:?}"));
        assert!(args_line.contains(REPORT_RESULT_MCP_SUBCOMMAND), "{args_line}");
        assert!(args_line.contains("sess-123"), "{args_line}");
        assert!(args_line.contains("/run/agentd.sock"), "{args_line}");
    }

    #[test]
    fn build_claude_argv_wires_report_result_stdio_mcp_server() {
        let params = launch_params_for("claude", None);
        let channel = test_result_channel();
        let argv = build_claude_argv(&params, Some(&channel));
        let cfg_pos = argv
            .iter()
            .position(|a| a == "--mcp-config")
            .expect("claude argv must carry --mcp-config");
        let cfg: Value =
            serde_json::from_str(&argv[cfg_pos + 1]).expect("mcp-config must be valid JSON");
        let server = &cfg["mcpServers"][REPORT_RESULT_MCP_SERVER];
        assert_eq!(server["type"], "stdio", "config: {}", argv[cfg_pos + 1]);
        assert_eq!(server["command"], "/opt/doeff-agentd");
        let args = server["args"].as_array().expect("stdio args array");
        assert_eq!(args[0], REPORT_RESULT_MCP_SUBCOMMAND);
        assert!(args.iter().any(|a| a == "sess-123"));
        assert!(
            argv.iter().any(|a| a == "--strict-mcp-config"),
            "claude argv must restrict to the wired servers: {argv:?}"
        );
    }

    #[test]
    fn build_claude_argv_has_no_mcp_config_without_a_channel_or_caller_servers() {
        let params = launch_params_for("claude", None);
        let argv = build_claude_argv(&params, None);
        assert!(
            !argv.iter().any(|a| a == "--mcp-config"),
            "no MCP config expected when there is nothing to wire: {argv:?}"
        );
    }

    fn snapshot_for_lifecycle(lifecycle: &str, status: &str) -> SessionSnapshot {
        SessionSnapshot {
            session_id: String::from("s1"),
            session_name: String::from("s1"),
            pane_id: String::from("%1"),
            agent_type: String::from("codex"),
            work_dir: String::from("/tmp"),
            lifecycle: String::from(lifecycle),
            status: String::from(status),
            backend_kind: String::from("tmux"),
            backend_ref: BTreeMap::new(),
            started_at: now_iso(),
            last_observed_at: None,
            finished_at: None,
            cleaned_at: None,
            pr_url: None,
            output_snippet: None,
            terminal_cause: None,
            expected_result: None,
            retries_used: 0,
            last_validation_error: None,
            awaiting_response: false,
            observed_active_at: None,
            result_payload: None,
            result_solicitations_used: 0,
            prompt_unblock_attempts: 0,
            last_output_change_at: None,
        }
    }

    /// Build an `agent_sessions` row directly via `upsert_snapshot`,
    /// bypassing tmux.  Tests for `session.await_result` need to flip
    /// the status field without going through the launch pathway,
    /// which would require a live tmux server.
    fn insert_test_snapshot(
        conn: &Connection,
        session_id: &str,
        status: &str,
        work_dir: &str,
        expected_result: Option<ExpectedResultSpec>,
        last_validation_error: Option<String>,
        output_snippet: Option<String>,
        result_payload: Option<String>,
    ) {
        let snapshot = SessionSnapshot {
            session_id: String::from(session_id),
            session_name: String::from(session_id),
            pane_id: String::from("%1"),
            agent_type: String::from("codex"),
            work_dir: String::from(work_dir),
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            status: String::from(status),
            backend_kind: String::from("tmux"),
            backend_ref: BTreeMap::new(),
            started_at: now_iso(),
            last_observed_at: None,
            finished_at: None,
            cleaned_at: None,
            pr_url: None,
            output_snippet,
            terminal_cause: None,
            expected_result,
            retries_used: 0,
            last_validation_error,
            awaiting_response: false,
            observed_active_at: None,
            result_payload,
            result_solicitations_used: 0,
            prompt_unblock_attempts: 0,
            last_output_change_at: None,
        };
        upsert_snapshot(conn, &snapshot).expect("upsert test snapshot");
    }

    #[test]
    fn await_result_returns_terminal_session_with_null_result_when_no_contract() {
        // Spec test #1: a session that exits cleanly with no
        // expected_result contract must return `result: null` and the
        // snapshot in a terminal state.
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        insert_test_snapshot(
            &conn,
            "await-no-contract",
            "exited",
            "/tmp",
            None,
            None,
            None,
            None,
        );

        let value = session_await_result_with_interval(
            &conn,
            AwaitResultParams {
                session_id: String::from("await-no-contract"),
                timeout_seconds: Some(2.0),
            },
            Duration::from_millis(50),
        )
        .expect("await_result should succeed");

        assert_eq!(value.get("result"), Some(&Value::Null));
        let session = value.get("session").expect("session field");
        assert_eq!(
            session.get("status").and_then(|v| v.as_str()),
            Some("exited")
        );
        assert!(
            value.get("validation_error").is_none(),
            "no contract ⇒ no validation_error: got {value:?}"
        );
    }

    #[test]
    fn await_result_returns_parsed_payload_when_contract_validates() {
        // Spec test #2: contract present, a result reported over the
        // report_result channel and persisted -> response carries the
        // parsed result under `payload`. (ADR 0035: no transcript source.)
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");

        let verdict_schema = serde_json::json!({
            "type": "object",
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string", "minLength": 1}}
        });
        let spec = schema_only_spec(verdict_schema);
        insert_test_snapshot(
            &conn,
            "await-with-contract",
            "done",
            "/tmp",
            Some(spec),
            None,
            None,
            Some(String::from(r#"{"verdict":"ok","notes":"clean"}"#)),
        );

        let value = session_await_result_with_interval(
            &conn,
            AwaitResultParams {
                session_id: String::from("await-with-contract"),
                timeout_seconds: Some(2.0),
            },
            Duration::from_millis(50),
        )
        .expect("await_result should succeed");

        let result = value.get("result").expect("result field");
        assert!(result.is_object(), "result must be an object: {value:?}");
        assert_eq!(
            result
                .get("payload")
                .and_then(|v| v.get("verdict"))
                .and_then(|v| v.as_str()),
            Some("ok")
        );
        assert!(value.get("validation_error").is_none());
    }

    #[test]
    fn await_result_serves_persisted_payload_after_worktree_reaped() {
        // Regression (result-payload loss): the monitor validates the
        // transcript block and persists the payload, then cleanup reaps the
        // agent's terminal/worktree. await must serve the persisted payload
        // rather than depending on any external state.
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");

        // A work_dir that does NOT exist on disk — the worktree was reaped.
        let reaped_work_dir = tmp.path().join("reaped-worktree");
        assert!(!reaped_work_dir.exists());

        let verdict_schema = serde_json::json!({
            "type": "object",
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string", "minLength": 1}}
        });
        let snapshot = SessionSnapshot {
            session_id: String::from("await-reaped"),
            session_name: String::from("await-reaped"),
            pane_id: String::from("%1"),
            agent_type: String::from("codex"),
            work_dir: reaped_work_dir.to_string_lossy().into_owned(),
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            status: String::from("done"),
            backend_kind: String::from("tmux"),
            backend_ref: BTreeMap::new(),
            started_at: now_iso(),
            last_observed_at: None,
            finished_at: Some(now_iso()),
            cleaned_at: Some(now_iso()),
            pr_url: None,
            output_snippet: None,
            terminal_cause: None,
            expected_result: Some(schema_only_spec(verdict_schema)),
            retries_used: 0,
            last_validation_error: None,
            awaiting_response: false,
            observed_active_at: None,
            result_payload: Some(String::from(r#"{"verdict":"ok","notes":"clean"}"#)),
            result_solicitations_used: 0,
            prompt_unblock_attempts: 0,
            last_output_change_at: None,
        };
        // Round-trips through the DB: upsert → session_get → row_to_snapshot
        // → build_await_response, so it also exercises the new column.
        upsert_snapshot(&conn, &snapshot).expect("upsert reaped snapshot");

        let value = session_await_result_with_interval(
            &conn,
            AwaitResultParams {
                session_id: String::from("await-reaped"),
                timeout_seconds: Some(2.0),
            },
            Duration::from_millis(50),
        )
        .expect("await_result should succeed");

        let result = value.get("result").expect("result field");
        assert_eq!(
            result
                .get("payload")
                .and_then(|v| v.get("verdict"))
                .and_then(|v| v.as_str()),
            Some("ok"),
            "persisted payload must be served even though work_dir is gone: {value:?}"
        );
        assert!(
            value.get("validation_error").is_none(),
            "persisted payload ⇒ no validation_error: {value:?}"
        );
    }

    #[test]
    fn await_result_returns_timeout_error_when_session_never_reaches_terminal() {
        // Spec test #3: contract present, session stays non-terminal,
        // await must return a -32000 timeout error.
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");

        let spec = schema_only_spec(serde_json::json!({"type": "object"}));
        insert_test_snapshot(
            &conn,
            "await-timeout",
            "running",
            "/tmp",
            Some(spec),
            None,
            None,
            None,
        );

        let err = session_await_result_with_interval(
            &conn,
            AwaitResultParams {
                session_id: String::from("await-timeout"),
                // Below the documented minimum, gets clamped up to 1s.
                timeout_seconds: Some(0.5),
            },
            Duration::from_millis(50),
        )
        .expect_err("await_result must time out");

        let rpc_err = err
            .downcast_ref::<RpcError>()
            .expect("must be an RpcError, got plain anyhow");
        assert_eq!(rpc_err.code, RPC_ERR_AWAIT_TIMEOUT);
        assert!(
            rpc_err.message.contains("timed out"),
            "unexpected message: {}",
            rpc_err.message
        );
        assert!(
            rpc_err.message.contains("await-timeout"),
            "message must include the session id: {}",
            rpc_err.message
        );
    }

    #[test]
    fn await_result_returns_no_such_session_for_unknown_id() {
        // Spec test #4: unknown session id → -32001 error, fast path
        // (no polling).
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");

        let err = session_await_result_with_interval(
            &conn,
            AwaitResultParams {
                session_id: String::from("does-not-exist"),
                timeout_seconds: Some(2.0),
            },
            Duration::from_millis(50),
        )
        .expect_err("await_result on missing session must error");

        let rpc_err = err
            .downcast_ref::<RpcError>()
            .expect("must be an RpcError, got plain anyhow");
        assert_eq!(rpc_err.code, RPC_ERR_NO_SUCH_SESSION);
        assert!(
            rpc_err.message.contains("does-not-exist"),
            "message must include the session id: {}",
            rpc_err.message
        );
    }

    #[test]
    fn await_result_surfaces_validation_error_when_contract_fails_post_done() {
        // Defensive cross-cutting check: a contract session that somehow
        // landed in `done` with no reported result payload must yield
        // result: null AND surface a reason so the Haskell client can
        // branch on it. (ADR 0035: `done` normally implies a persisted
        // report_result payload; this guards the should-not-happen case.)
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");

        let spec = schema_only_spec(serde_json::json!({"type": "object"}));
        insert_test_snapshot(
            &conn,
            "await-missing-block",
            "done",
            "/tmp",
            Some(spec),
            None,
            None,
            None,
        );

        let value = session_await_result_with_interval(
            &conn,
            AwaitResultParams {
                session_id: String::from("await-missing-block"),
                timeout_seconds: Some(2.0),
            },
            Duration::from_millis(50),
        )
        .expect("await_result returns ok-with-null even on contract failure");

        assert_eq!(value.get("result"), Some(&Value::Null));
        let reason = value
            .get("validation_error")
            .and_then(|v| v.as_str())
            .expect("validation_error string must be present");
        assert!(
            reason.contains("without a reported result payload"),
            "expected missing-result reason, got: {reason}"
        );
    }

    // ---- ADR 0035: report_result data channel + reject-at-launch gate ----

    fn open_migrated_db() -> (tempfile::TempDir, Connection) {
        let tmp = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(tmp.path().join("agentd.sqlite")).expect("open sqlite");
        migrate(&conn).expect("migrate");
        (tmp, conn)
    }

    fn permissive_summary_spec() -> ExpectedResultSpec {
        schema_only_spec(serde_json::json!({
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string", "minLength": 1}}
        }))
    }

    #[test]
    fn report_result_persists_payload_byte_faithfully() {
        // The core ADR 0035 property at the RPC boundary: a payload whose
        // string values contain exactly the content a fixed-width terminal
        // grid would corrupt (word-boundary spaces, runs of spaces, a tab,
        // a trailing space) round-trips byte-for-byte through the data
        // channel. A screen scrape could never preserve these.
        let (_tmp, conn) = open_migrated_db();
        insert_test_snapshot(
            &conn,
            "byte-faithful",
            "running",
            "/tmp",
            Some(permissive_summary_spec()),
            None,
            None,
            None,
        );
        let tricky = "ACPresult notevalidating — value with  double  spaces and\ttab and trailing ";
        let payload = serde_json::json!({
            "summary": tricky,
            "pr_url": "https://github.com/acme/proboscis-ema/pull/594"
        });

        let ok = session_report_result(
            &conn,
            ReportResultParams {
                session_id: String::from("byte-faithful"),
                payload: payload.clone(),
            },
        )
        .expect("valid payload is accepted");
        assert_eq!(ok.get("accepted").and_then(Value::as_bool), Some(true));

        // The recovered payload equals what the agent emitted, exactly.
        let stored = current_result_payload(&conn, "byte-faithful")
            .expect("read")
            .expect("payload persisted");
        let recovered: Value = serde_json::from_str(&stored).expect("stored payload is JSON");
        assert_eq!(recovered, payload, "recovered payload must be byte-identical");
        assert_eq!(
            recovered.get("summary").and_then(Value::as_str),
            Some(tricky),
            "the exact whitespace of the string value must survive"
        );
    }

    #[test]
    fn report_result_rejects_schema_invalid_payload_without_persisting() {
        // Deterministic validation failure (ADR 0035 R4 / hard rule 7):
        // the payload is not persisted, the agent is told the reason, and
        // nothing is retried.
        let (_tmp, conn) = open_migrated_db();
        insert_test_snapshot(
            &conn,
            "invalid-payload",
            "running",
            "/tmp",
            Some(permissive_summary_spec()),
            None,
            None,
            None,
        );

        let err = session_report_result(
            &conn,
            ReportResultParams {
                session_id: String::from("invalid-payload"),
                payload: serde_json::json!({"summary": ""}), // fails minLength
            },
        )
        .expect_err("schema-invalid payload must be rejected");
        let rpc = err.downcast_ref::<RpcError>().expect("structured error");
        assert_eq!(rpc.code, RPC_ERR_RESULT_REJECTED);
        assert!(
            current_result_payload(&conn, "invalid-payload")
                .expect("read")
                .is_none(),
            "an invalid payload must not be persisted"
        );
    }

    #[test]
    fn report_result_is_idempotent_first_write_wins() {
        let (_tmp, conn) = open_migrated_db();
        insert_test_snapshot(
            &conn,
            "idem",
            "running",
            "/tmp",
            Some(permissive_summary_spec()),
            None,
            None,
            None,
        );
        session_report_result(
            &conn,
            ReportResultParams {
                session_id: String::from("idem"),
                payload: serde_json::json!({"summary": "first"}),
            },
        )
        .expect("first report accepted");
        let second = session_report_result(
            &conn,
            ReportResultParams {
                session_id: String::from("idem"),
                payload: serde_json::json!({"summary": "second"}),
            },
        )
        .expect("second report is idempotent, not an error");
        assert_eq!(
            second.get("already_reported").and_then(Value::as_bool),
            Some(true)
        );
        let stored = current_result_payload(&conn, "idem").expect("read").expect("payload");
        let recovered: Value = serde_json::from_str(&stored).unwrap();
        assert_eq!(
            recovered.get("summary").and_then(Value::as_str),
            Some("first"),
            "first write wins"
        );
    }

    #[test]
    fn report_result_rejected_after_terminal_without_result() {
        let (_tmp, conn) = open_migrated_db();
        insert_test_snapshot(
            &conn,
            "already-failed",
            "failed",
            "/tmp",
            Some(permissive_summary_spec()),
            None,
            None,
            None,
        );
        let err = session_report_result(
            &conn,
            ReportResultParams {
                session_id: String::from("already-failed"),
                payload: serde_json::json!({"summary": "too late"}),
            },
        )
        .expect_err("a report after terminal-without-result must be rejected");
        let rpc = err.downcast_ref::<RpcError>().expect("structured error");
        assert_eq!(rpc.code, RPC_ERR_ALREADY_TERMINAL);
    }

    fn gate_config(tmp: &tempfile::TempDir) -> Config {
        Config {
            db_path: tmp.path().join("agentd.sqlite"),
            socket_path: tmp.path().join("agentd.sock"),
            // `has-session` always exits non-zero → no pre-existing session,
            // so session_launch reaches the reject-at-launch gate. A
            // rejected launch never calls `new-session`, so `false` suffices.
            tmux_bin: String::from("false"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 4,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        }
    }

    fn contract_launch_params(agent_type: &str) -> LaunchParams {
        // ADR-DOE-AGENTS-003: codex launches must pin their auth profile;
        // the gate tests target OTHER gates, so satisfy this one up front.
        let mut session_env = BTreeMap::new();
        session_env.insert(
            String::from("CODEX_HOME"),
            String::from("/profiles/company-test"),
        );
        LaunchParams {
            session_id: format!("gate-{agent_type}"),
            session_name: format!("gate-{agent_type}"),
            agent_type: String::from(agent_type),
            work_dir: String::from("/tmp"),
            command: None,
            prompt: Some(String::from("do the task")),
            model: None,
            effort: None,
            mcp_servers: BTreeMap::new(),
            skip_trust_setup: true,
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            session_env,
            expected_result: Some(schema_only_spec(serde_json::json!({"type": "object"}))),
        }
    }

    #[test]
    fn session_launch_rejects_codex_without_explicit_auth_profile() {
        // ADR-DOE-AGENTS-003: no default agent auth.  A codex launch that
        // pins no CODEX_HOME (neither session_env nor command) is rejected
        // before any tmux work.
        let tmp = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(tmp.path().join("agentd.sqlite")).expect("db");
        migrate(&conn).expect("migrate");
        let config = gate_config(&tmp);
        let mut params = contract_launch_params("codex");
        params.session_env.clear();
        let err = session_launch(&conn, &config, params)
            .expect_err("codex without an auth profile must be rejected");
        let msg = format!("{err:#}");
        assert!(
            msg.contains("no agent auth profile") && msg.contains("CODEX_HOME"),
            "gate error must name the missing profile and the remedy: {msg}"
        );
    }

    #[test]
    fn session_launch_accepts_codex_auth_via_command_override() {
        // An explicit command that pins CODEX_HOME satisfies the gate even
        // with an empty session_env (the ACP catalog shape:
        // `env CODEX_HOME=... codex`).
        let tmp = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(tmp.path().join("agentd.sqlite")).expect("db");
        migrate(&conn).expect("migrate");
        let config = gate_config(&tmp);
        let mut params = contract_launch_params("codex");
        params.session_env.clear();
        params.command = Some(String::from(
            "env CODEX_HOME=/profiles/company-test codex",
        ));
        let err = session_launch(&conn, &config, params)
            .expect_err("fake tmux cannot actually launch");
        let msg = format!("{err:#}");
        assert!(
            !msg.contains("no agent auth profile"),
            "explicit command auth must pass the gate, got: {msg}"
        );
    }

    #[test]
    fn command_mentions_codex_matches_tokens_not_substrings() {
        assert!(command_mentions_codex("codex"));
        assert!(command_mentions_codex("env CODEX_HOME=/x codex --yolo"));
        assert!(command_mentions_codex("/usr/local/bin/codex"));
        assert!(!command_mentions_codex("true"));
        assert!(!command_mentions_codex("codexify --all"));
    }

    #[test]
    fn session_launch_rejects_contract_for_unwireable_agent() {
        // ADR 0035 reject-at-launch: an agent agentd cannot wire the
        // report_result channel into (here: gemini) cannot deliver a result,
        // so a contract launch is rejected up front.
        let tmp = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(tmp.path().join("agentd.sqlite")).expect("db");
        migrate(&conn).expect("migrate");
        let config = gate_config(&tmp);
        let err = session_launch(&conn, &config, contract_launch_params("gemini"))
            .expect_err("gemini contract launch must be rejected");
        let msg = format!("{err:#}");
        assert!(
            msg.contains(REPORT_RESULT_TOOL) && msg.contains("codex' or 'claude'"),
            "gate error must explain the missing result channel: {msg}"
        );
    }

    #[test]
    fn session_launch_lets_codex_contract_through_the_gate() {
        // A codex contract launch passes the gate (agentd wires the channel);
        // it fails later only because the fake tmux cannot start a session —
        // proving the gate itself did not reject it.
        let tmp = tempfile::tempdir().expect("tempdir");
        let conn = Connection::open(tmp.path().join("agentd.sqlite")).expect("db");
        migrate(&conn).expect("migrate");
        let config = gate_config(&tmp);
        let err = session_launch(&conn, &config, contract_launch_params("codex"))
            .expect_err("fake tmux cannot actually launch");
        let msg = format!("{err:#}");
        assert!(
            !msg.contains("cannot deliver a result"),
            "codex must pass the result-channel gate, got: {msg}"
        );
    }

    fn write_launch_fake_tmux(
        tmp: &tempfile::TempDir,
        ready_frame: &str,
        block_on_load: bool,
    ) -> (String, PathBuf, PathBuf) {
        let tmux_bin = tmp.path().join("fake-launch-tmux");
        let load_started = tmp.path().join("load-started");
        let load_release = tmp.path().join("load-release");
        let block = if block_on_load {
            format!(
                ": > '{started}'\nwhile [ ! -e '{release}' ]; do sleep 0.01; done\n",
                started = load_started.display(),
                release = load_release.display(),
            )
        } else {
            String::new()
        };
        fs::write(
            &tmux_bin,
            format!(
                "#!/bin/sh\ncase \"$1\" in\n  has-session) exit 1 ;;\n  new-session) printf '%%7\\n' ;;\n  load-buffer) {block}cat >/dev/null ;;\n  capture-pane) printf '%s\\n' '{ready_frame}' ;;\nesac\nexit 0\n"
            ),
        )
        .expect("write launch fake tmux");
        let mut perms = fs::metadata(&tmux_bin).expect("metadata").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&tmux_bin, perms).expect("chmod launch fake tmux");
        (
            tmux_bin.to_string_lossy().into_owned(),
            load_started,
            load_release,
        )
    }

    fn registered_launch_params(session_id: &str) -> LaunchParams {
        let mut params = launch_params_for("codex", None);
        params.session_id = String::from(session_id);
        params.session_name = String::from(session_id);
        params.session_env.insert(
            String::from("CODEX_HOME"),
            String::from("/profiles/company-test"),
        );
        params
    }

    #[test]
    fn session_launch_registers_booting_row_before_ready_gate() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let observer = Connection::open(&db).expect("observer db");
        migrate(&observer).expect("migrate");
        let (tmux_bin, load_started, load_release) = write_launch_fake_tmux(&tmp, "› ready", true);
        let config = Config {
            db_path: db.clone(),
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin,
            monitor_interval: Duration::from_millis(1000),
            max_running: 4,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };
        let params = registered_launch_params("register-before-ready");
        let launch_db = db.clone();
        let launch = thread::spawn(move || {
            let conn = Connection::open(launch_db).expect("launch db");
            session_launch(&conn, &config, params)
        });

        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while !load_started.exists() && std::time::Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(
            load_started.exists(),
            "launch command did not reach fake tmux"
        );
        let observed = session_get(&observer, "register-before-ready").expect("read during launch");
        fs::write(&load_release, "release").expect("release launch");
        let result = launch.join().expect("launch thread");

        assert!(
            result.is_ok(),
            "launch should finish after ready frame: {result:?}"
        );
        let observed = observed.expect("BOOTING row must be visible while launch is blocked");
        assert_eq!(observed.status, "booting");
        assert!(!observed.awaiting_response);
    }

    #[test]
    fn session_launch_ready_timeout_terminalizes_registered_row() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("db");
        migrate(&conn).expect("migrate");
        let (tmux_bin, _load_started, _load_release) =
            write_launch_fake_tmux(&tmp, "still booting", false);
        let config = Config {
            db_path: db,
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin,
            monitor_interval: Duration::from_millis(1000),
            max_running: 4,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: None,
        };

        let error = session_launch_with_ready_timeout(
            &conn,
            &config,
            registered_launch_params("ready-timeout"),
            Duration::from_millis(20),
        )
        .expect_err("ready timeout must fail launch");

        assert!(error.to_string().contains("did not become ready"));
        let snapshot = session_get(&conn, "ready-timeout")
            .expect("read terminal row")
            .expect("registered row survives as lifecycle history");
        assert_eq!(snapshot.status, "failed");
        assert!(snapshot.finished_at.is_some());
        assert!(snapshot.cleaned_at.is_some());
        let booting_count: i64 = conn
            .query_row(
                "SELECT count(*) FROM agent_sessions WHERE status = 'booting'",
                [],
                |row| row.get(0),
            )
            .expect("count booting rows");
        assert_eq!(booting_count, 0);
    }

    // ---- stdio MCP server (report-result-mcp subcommand) ----

    #[test]
    fn mcp_initialize_echoes_protocol_and_advertises_tools() {
        let msg = serde_json::json!({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}
        });
        let resp = handle_mcp_message(&msg, "s1", "/nonexistent.sock").expect("initialize responds");
        assert_eq!(resp["result"]["protocolVersion"], "2025-06-18");
        assert!(resp["result"]["capabilities"]["tools"].is_object());
    }

    #[test]
    fn mcp_tools_list_exposes_report_result() {
        let msg = serde_json::json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"});
        let resp = handle_mcp_message(&msg, "s1", "/nonexistent.sock").expect("tools/list responds");
        let tools = resp["result"]["tools"].as_array().expect("tools array");
        assert_eq!(tools[0]["name"], REPORT_RESULT_TOOL);
        assert_eq!(tools[0]["inputSchema"]["required"][0], "payload");
    }

    #[test]
    fn mcp_notifications_get_no_response() {
        let msg = serde_json::json!({"jsonrpc": "2.0", "method": "notifications/initialized"});
        assert!(handle_mcp_message(&msg, "s1", "/nonexistent.sock").is_none());
    }

    #[test]
    fn mcp_unknown_tool_is_a_tool_error() {
        let msg = serde_json::json!({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "not_report_result", "arguments": {}}
        });
        let resp = handle_mcp_message(&msg, "s1", "/nonexistent.sock").expect("tools/call responds");
        assert_eq!(resp["result"]["isError"], true);
    }

    // ---- ADR-DOE-AGENTS-002: result solicitation + interactive-prompt
    // watchdog ------------------------------------------------------------

    /// Fake tmux for monitor-loop tests: logs every invocation, answers
    /// `has-session` with success, serves `capture-pane` from a file so
    /// tests control exactly what the monitor "sees", and reports the
    /// pane's current command as the agent binary (not an idle shell).
    fn write_monitor_fake_tmux(
        tmp: &tempfile::TempDir,
        pane_file: &std::path::Path,
        log_file: &std::path::Path,
    ) -> String {
        let tmux_bin = tmp.path().join("fake-tmux");
        fs::write(
            &tmux_bin,
            format!(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{log}'\ncase \"$1\" in\n  has-session) exit 0 ;;\n  capture-pane) cat '{pane}' ;;\n  display-message) printf 'codex\\n' ;;\nesac\nexit 0\n",
                log = log_file.display(),
                pane = pane_file.display()
            ),
        )
        .expect("write fake tmux");
        let mut perms = fs::metadata(&tmux_bin).expect("metadata").permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&tmux_bin, perms).expect("chmod fake tmux");
        tmux_bin.to_string_lossy().into_owned()
    }

    fn monitor_test_config(
        tmp: &tempfile::TempDir,
        db: PathBuf,
        tmux_bin: String,
        judge_cmd: Option<String>,
    ) -> Config {
        Config {
            db_path: db,
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin,
            monitor_interval: Duration::from_millis(1000),
            max_running: 10,
            result_solicitation_limit: DEFAULT_RESULT_SOLICITATION_LIMIT,
            prompt_stall_seconds: DEFAULT_PROMPT_STALL_SECONDS,
            prompt_unblock_limit: DEFAULT_PROMPT_UNBLOCK_LIMIT,
            prompt_judge_cmd: judge_cmd,
        }
    }

    /// A running run_to_completion session that is past startup, recently
    /// observed, and whose output snippet equals `pane` — so the very next
    /// monitor tick sees a STABLE capture.
    fn stable_monitor_snapshot(pane: &str, expected: Option<ExpectedResultSpec>) -> SessionSnapshot {
        let mut snapshot = snapshot_for_lifecycle(LIFECYCLE_RUN_TO_COMPLETION, "running");
        snapshot.expected_result = expected;
        snapshot.output_snippet = Some(tail_chars(pane, 500));
        snapshot.observed_active_at = Some(now_iso());
        snapshot.last_observed_at = Some(now_iso());
        snapshot
    }

    fn session_event_types(conn: &Connection, session_id: &str) -> Vec<String> {
        let mut stmt = conn
            .prepare(
                "SELECT event_type FROM agent_session_events WHERE session_id = ?1 ORDER BY id",
            )
            .expect("prepare events query");
        let rows = stmt
            .query_map(params![session_id], |row| row.get::<_, String>(0))
            .expect("query events");
        rows.filter_map(|r| r.ok()).collect()
    }

    fn verdict_schema_spec() -> ExpectedResultSpec {
        schema_only_spec(serde_json::json!({
            "type": "object",
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string", "minLength": 1}}
        }))
    }

    #[test]
    fn turn_end_without_result_solicits_before_failing() {
        // ADR-DOE-AGENTS-002 R1/R2/R4: the first turn-end without a valid
        // reported result sends the corrective solicitation and keeps the
        // session non-terminal instead of failing it.
        let tmp = tempfile::tempdir().expect("tempdir");
        let pane = "agent output scrolled by\n› ";
        let pane_file = tmp.path().join("pane.txt");
        fs::write(&pane_file, pane).expect("write pane");
        let log_file = tmp.path().join("tmux.log");
        let tmux_bin = write_monitor_fake_tmux(&tmp, &pane_file, &log_file);
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        upsert_snapshot(&conn, &stable_monitor_snapshot(pane, Some(verdict_schema_spec())))
            .expect("insert session");
        let config = monitor_test_config(&tmp, db, tmux_bin, None);

        monitor_once(&config).expect("monitor tick");

        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.status, "running", "solicitation must stay non-terminal");
        assert_eq!(session.result_solicitations_used, 1);
        assert!(session.awaiting_response, "latch re-armed after solicitation");
        assert!(
            session_event_types(&conn, "s1").contains(&String::from("session_result_solicited")),
            "solicitation event recorded"
        );
        let log = fs::read_to_string(&log_file).expect("read tmux log");
        assert!(
            log.contains("paste-buffer"),
            "solicitation message pasted into the pane: {log}"
        );

        // A second tick while the latch is armed must NOT double-solicit:
        // turn-end is suppressed until the agent visibly picks the message
        // up (active marker), so the counter stays at 1.
        monitor_once(&config).expect("second monitor tick");
        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.result_solicitations_used, 1);
        assert_eq!(session.status, "running");
    }

    #[test]
    fn turn_end_fails_terminal_without_result_after_solicitation_budget() {
        // ADR-DOE-AGENTS-002 R2: an exhausted solicitation budget finalises
        // through the existing terminal-without-result failure (the ACP
        // discriminator is unchanged, the reason names the solicitations).
        let tmp = tempfile::tempdir().expect("tempdir");
        let pane = "agent output scrolled by\n› ";
        let pane_file = tmp.path().join("pane.txt");
        fs::write(&pane_file, pane).expect("write pane");
        let log_file = tmp.path().join("tmux.log");
        let tmux_bin = write_monitor_fake_tmux(&tmp, &pane_file, &log_file);
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let mut snapshot = stable_monitor_snapshot(pane, Some(verdict_schema_spec()));
        snapshot.result_solicitations_used = DEFAULT_RESULT_SOLICITATION_LIMIT;
        upsert_snapshot(&conn, &snapshot).expect("insert session");
        let config = monitor_test_config(&tmp, db, tmux_bin, None);

        monitor_once(&config).expect("monitor tick");

        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.status, "failed");
        let reason = session.last_validation_error.expect("validation error");
        assert!(
            reason.contains("without reporting a result via report_result"),
            "discriminator-facing reason preserved: {reason}"
        );
        assert!(
            reason.contains("after 2 solicitation(s)"),
            "reason names the exhausted budget: {reason}"
        );
    }

    #[test]
    fn turn_end_menu_is_unblocked_by_judge_not_solicited() {
        // ADR-DOE-AGENTS-002 R6: a codex menu renders the idle-prompt glyph,
        // so the turn-end site must consult the judge BEFORE pasting the
        // solicitation (whose submit Enter would confirm an arbitrary menu
        // option).
        let tmp = tempfile::tempdir().expect("tempdir");
        let pane = "Approaching usage limits\n\
                    Switch to a smaller model for lower credit usage?\n\
                    › 1. Switch to mini\n  2. Keep current model\nPress enter to confirm\n";
        let pane_file = tmp.path().join("pane.txt");
        fs::write(&pane_file, pane).expect("write pane");
        let log_file = tmp.path().join("tmux.log");
        let tmux_bin = write_monitor_fake_tmux(&tmp, &pane_file, &log_file);
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        upsert_snapshot(&conn, &stable_monitor_snapshot(pane, Some(verdict_schema_spec())))
            .expect("insert session");
        let judge = String::from(
            r#"printf '{"blocked": true, "keys": ["Down", "Enter"], "reason": "confirmation menu"}'"#,
        );
        let config = monitor_test_config(&tmp, db, tmux_bin, Some(judge));

        monitor_once(&config).expect("monitor tick");

        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.status, "running");
        assert_eq!(session.prompt_unblock_attempts, 1);
        assert_eq!(
            session.result_solicitations_used, 0,
            "no solicitation was pasted into the menu"
        );
        assert!(
            session_event_types(&conn, "s1").contains(&String::from("session_prompt_unblocked")),
            "unblock event recorded"
        );
        let log = fs::read_to_string(&log_file).expect("read tmux log");
        assert!(log.contains("Down"), "menu navigation key sent: {log}");
        assert!(
            !log.contains("paste-buffer"),
            "no solicitation paste into a menu: {log}"
        );
    }

    #[test]
    fn stalled_non_prompt_pane_fails_typed_when_no_judge_configured() {
        // ADR-DOE-AGENTS-002 R5/R7: a frozen non-REPL pane (login prompt,
        // pager, unknown dialog) can never reach turn-end; past the stall
        // threshold it must fail loudly with the typed cause — with no
        // judge configured there is nothing else that can unblock it.
        let tmp = tempfile::tempdir().expect("tempdir");
        let pane = "Enter password for proxy: ";
        let pane_file = tmp.path().join("pane.txt");
        fs::write(&pane_file, pane).expect("write pane");
        let log_file = tmp.path().join("tmux.log");
        let tmux_bin = write_monitor_fake_tmux(&tmp, &pane_file, &log_file);
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let mut snapshot = stable_monitor_snapshot(pane, None);
        snapshot.last_output_change_at = Some(
            (Utc::now() - ChronoDuration::seconds(DEFAULT_PROMPT_STALL_SECONDS + 120))
                .to_rfc3339(),
        );
        upsert_snapshot(&conn, &snapshot).expect("insert session");
        let config = monitor_test_config(&tmp, db, tmux_bin, None);

        monitor_once(&config).expect("monitor tick");

        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.status, "failed");
        let reason = session.last_validation_error.expect("validation error");
        assert!(
            reason.starts_with("interactive-prompt-blocked:"),
            "typed reason prefix: {reason}"
        );
        let cause = session.terminal_cause.expect("terminal cause");
        assert!(matches!(
            cause.category,
            TerminalCauseCategory::InteractivePromptBlocked
        ));
    }

    #[test]
    fn stalled_pane_is_unblocked_by_judge_and_stays_running() {
        // ADR-DOE-AGENTS-002 R5: the judge clears a blocked prompt and the
        // session keeps running under the bounded attempt budget.
        let tmp = tempfile::tempdir().expect("tempdir");
        let pane = "Do you want to continue? [y/N] ";
        let pane_file = tmp.path().join("pane.txt");
        fs::write(&pane_file, pane).expect("write pane");
        let log_file = tmp.path().join("tmux.log");
        let tmux_bin = write_monitor_fake_tmux(&tmp, &pane_file, &log_file);
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let mut snapshot = stable_monitor_snapshot(pane, None);
        snapshot.last_output_change_at = Some(
            (Utc::now() - ChronoDuration::seconds(DEFAULT_PROMPT_STALL_SECONDS + 120))
                .to_rfc3339(),
        );
        upsert_snapshot(&conn, &snapshot).expect("insert session");
        let judge = String::from(
            r#"printf '{"blocked": true, "keys": ["y", "Enter"], "reason": "y/N confirmation"}'"#,
        );
        let config = monitor_test_config(&tmp, db, tmux_bin, Some(judge));

        monitor_once(&config).expect("monitor tick");

        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.status, "running");
        assert_eq!(session.prompt_unblock_attempts, 1);
        assert!(
            session_event_types(&conn, "s1").contains(&String::from("session_prompt_unblocked"))
        );
        let log = fs::read_to_string(&log_file).expect("read tmux log");
        assert!(log.contains("send-keys"), "unblock keys sent: {log}");
    }

    #[test]
    fn stalled_pane_fails_typed_after_unblock_budget_exhausted() {
        // ADR-DOE-AGENTS-002 R7: the attempt budget is the hard stop —
        // never an infinite judge loop.
        let tmp = tempfile::tempdir().expect("tempdir");
        let pane = "Enter password for proxy: ";
        let pane_file = tmp.path().join("pane.txt");
        fs::write(&pane_file, pane).expect("write pane");
        let log_file = tmp.path().join("tmux.log");
        let tmux_bin = write_monitor_fake_tmux(&tmp, &pane_file, &log_file);
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let mut snapshot = stable_monitor_snapshot(pane, None);
        snapshot.prompt_unblock_attempts = DEFAULT_PROMPT_UNBLOCK_LIMIT;
        snapshot.last_output_change_at = Some(
            (Utc::now() - ChronoDuration::seconds(DEFAULT_PROMPT_STALL_SECONDS + 120))
                .to_rfc3339(),
        );
        upsert_snapshot(&conn, &snapshot).expect("insert session");
        let judge = String::from(r#"printf '{"blocked": true, "keys": ["Enter"], "reason": "x"}'"#);
        let config = monitor_test_config(&tmp, db, tmux_bin, Some(judge));

        monitor_once(&config).expect("monitor tick");

        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.status, "failed");
        let reason = session.last_validation_error.expect("validation error");
        assert!(reason.contains("unblock attempt(s) exhausted"), "{reason}");
    }

    #[test]
    fn active_working_pane_is_never_stall_judged() {
        // The stall watchdog must not fire while the agent is visibly
        // working — the active marker excludes it even if the clock says
        // the content has not changed.
        let tmp = tempfile::tempdir().expect("tempdir");
        let pane = "compiling...\n• Working (12s • esc to interrupt)\n";
        let pane_file = tmp.path().join("pane.txt");
        fs::write(&pane_file, pane).expect("write pane");
        let log_file = tmp.path().join("tmux.log");
        let tmux_bin = write_monitor_fake_tmux(&tmp, &pane_file, &log_file);
        let db = tmp.path().join("agentd.sqlite");
        let conn = Connection::open(&db).expect("open sqlite");
        migrate(&conn).expect("migrate");
        let mut snapshot = stable_monitor_snapshot(pane, None);
        snapshot.last_output_change_at = Some(
            (Utc::now() - ChronoDuration::seconds(DEFAULT_PROMPT_STALL_SECONDS + 120))
                .to_rfc3339(),
        );
        upsert_snapshot(&conn, &snapshot).expect("insert session");
        let config = monitor_test_config(&tmp, db, tmux_bin, None);

        monitor_once(&config).expect("monitor tick");

        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.status, "running");
        assert_eq!(session.prompt_unblock_attempts, 0);
        assert!(session.last_validation_error.is_none());
    }

    #[test]
    fn adr002_counters_round_trip_through_store() {
        // ADR-DOE-AGENTS-002: the correction counters are durable columns
        // that survive close/reopen (and, unlike awaiting_response, are
        // never reset by daemon startup).
        let tmp = tempfile::tempdir().expect("tempdir");
        let db = tmp.path().join("agentd.sqlite");
        {
            let conn = Connection::open(&db).expect("open sqlite");
            migrate(&conn).expect("migrate");
            let mut snapshot = snapshot_for_lifecycle(LIFECYCLE_RUN_TO_COMPLETION, "running");
            snapshot.result_solicitations_used = 2;
            snapshot.prompt_unblock_attempts = 1;
            snapshot.last_output_change_at = Some(String::from("2026-07-04T00:00:00Z"));
            upsert_snapshot(&conn, &snapshot).expect("insert");
        }
        let conn = Connection::open(&db).expect("reopen sqlite");
        migrate(&conn).expect("migrate is idempotent");
        let session = session_get(&conn, "s1").expect("get").expect("exists");
        assert_eq!(session.result_solicitations_used, 2);
        assert_eq!(session.prompt_unblock_attempts, 1);
        assert_eq!(
            session.last_output_change_at.as_deref(),
            Some("2026-07-04T00:00:00Z")
        );
    }

    #[test]
    fn prompt_judge_verdict_parses_json_embedded_in_prose() {
        let verdict = parse_prompt_judge_verdict(
            "Sure! Here is my verdict:\n{\"blocked\": true, \"keys\": [\"Down\", \"Enter\"], \"reason\": \"menu\"}\nHope that helps.",
        )
        .expect("verdict parses");
        assert!(verdict.blocked);
        assert_eq!(verdict.keys, vec!["Down", "Enter"]);
    }

    #[test]
    fn prompt_judge_verdict_rejects_disallowed_keys() {
        let err = parse_prompt_judge_verdict(
            r#"{"blocked": true, "keys": ["C-c"], "reason": "kill it"}"#,
        )
        .expect_err("control sequences are not allowed");
        assert!(err.to_string().contains("disallowed key"));
    }

    #[test]
    fn prompt_judge_verdict_rejects_blocked_without_keys() {
        let err = parse_prompt_judge_verdict(r#"{"blocked": true, "keys": [], "reason": "?"}"#)
            .expect_err("blocked verdict must carry keys");
        assert!(err.to_string().contains("no keys"));
    }

    #[test]
    fn prompt_judge_verdict_accepts_not_blocked_without_keys() {
        let verdict = parse_prompt_judge_verdict(r#"{"blocked": false, "reason": "idle REPL"}"#)
            .expect("not-blocked verdict parses");
        assert!(!verdict.blocked);
        assert!(verdict.keys.is_empty());
    }

    #[test]
    fn run_judge_command_times_out_hung_judge() {
        let err = run_judge_command("sleep 30", "pane", Duration::from_millis(300))
            .expect_err("hung judge must time out");
        assert!(err.to_string().contains("timed out"));
    }
}
