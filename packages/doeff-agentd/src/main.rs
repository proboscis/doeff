use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Duration as ChronoDuration, Utc};
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
const STALE_OBSERVATION_THRESHOLD_SECONDS: i64 = 300;
const LIFECYCLE_RUN_TO_COMPLETION: &str = "run_to_completion";
const LIFECYCLE_INTERACTIVE: &str = "interactive";

#[derive(Debug, Clone)]
struct Config {
    db_path: PathBuf,
    socket_path: PathBuf,
    tmux_bin: String,
    monitor_interval: Duration,
    max_running: usize,
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

#[derive(Debug, Serialize)]
struct RpcResponse {
    id: Value,
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

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
    /// Optional output-file contract.  When set, the monitor refuses to
    /// finalise the session as terminal until the named file exists and
    /// (when configured) matches the declared schema.  Missing or
    /// invalid output triggers an auto-retry up to `max_retries`
    /// times; exhausting retries marks the session as failed.
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
}

/// Contract the launcher attaches to a session to enforce input→output
/// semantics on top of doeff-agentd's existing terminal-detection
/// heuristics.  When set, the monitor validates the agent's output on
/// every transition to "done" before letting the session enter a
/// terminal state — and auto-prompts the agent to fix forgotten or
/// malformed outputs.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct ExpectedResultSpec {
    /// Path to the expected output, relative to `work_dir`.
    file_path: String,
    /// When set, the parsed JSON's top-level `schema` field must equal
    /// this name for validation to pass.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    schema_name: Option<String>,
    /// When set, the parsed JSON's top-level `schemaVersion` field
    /// must equal this integer.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    schema_version: Option<u32>,
    /// Message sent back to the agent when its output is missing or
    /// malformed.  The literal substring `%REASON%` is replaced with
    /// the validator's explanation so the agent has actionable
    /// feedback.
    #[serde(default = "default_retry_prompt")]
    retry_prompt: String,
    /// Maximum number of times the monitor re-prompts the agent before
    /// finalising the session as failed.  Counts retries only, not the
    /// initial run, so total attempts = max_retries + 1.
    #[serde(default = "default_max_retries")]
    max_retries: u32,
}

fn default_retry_prompt() -> String {
    String::from(
        "You exited without producing the required output file: %REASON%. \
         Re-read your previous instructions, write the file at the expected path \
         with the exact schema declared, and do not exit until the file is valid.",
    )
}

fn default_max_retries() -> u32 {
    2
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
    /// Optional output-file contract.  See 'ExpectedResultSpec' for the
    /// semantics; persisted with the session so the monitor can enforce
    /// it after the agent appears to finish.
    #[serde(default)]
    expected_result: Option<ExpectedResultSpec>,
}

#[derive(Debug, Deserialize)]
struct SessionIdParams {
    session_id: String,
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
    let config = parse_args(env::args().skip(1).collect())?;
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
    })
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
          output_snippet TEXT
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
    ensure_column(
        conn,
        "agent_sessions",
        "expected_result_json",
        "TEXT",
    )?;
    ensure_column(
        conn,
        "agent_sessions",
        "retries_used",
        "INTEGER NOT NULL DEFAULT 0",
    )?;
    ensure_column(
        conn,
        "agent_sessions",
        "last_validation_error",
        "TEXT",
    )?;
    ensure_column(
        conn,
        "agent_sessions",
        "awaiting_response",
        "INTEGER NOT NULL DEFAULT 0",
    )?;
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
        },
        Err(err) => RpcResponse {
            id,
            ok: false,
            result: None,
            error: Some(format!("{err:#}")),
        },
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
    } else {
        Err(anyhow!("unknown method: {}", request.method))
    }
}

/// Build the shell command line that tmux runs in the new pane.  Per-agent
/// adapters (codex, claude) own the argv shape; callers passing
/// `agent_type=generic` (or any unknown type) must provide `command`
/// explicitly as an escape hatch.
fn resolve_launch_command(params: &LaunchParams) -> Result<String> {
    if let Some(explicit) = params.command.as_ref() {
        if !explicit.trim().is_empty() {
            return Ok(explicit.clone());
        }
    }
    match params.agent_type.as_str() {
        "codex" => Ok(shell_join(build_codex_argv(params))),
        "claude" => Ok(shell_join(build_claude_argv(params))),
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

/// True when 'session_launch' should hand the prompt to the running
/// agent as an interactive message instead of baking it into the argv.
/// Currently codex and claude are the supported interactive agents;
/// callers using `agent_type=generic` (or anything else) opt out by
/// passing their own command + prompt argv explicitly.
fn uses_interactive_prompt(params: &LaunchParams) -> bool {
    if let Some(cmd) = params.command.as_ref() {
        if !cmd.trim().is_empty() {
            return false;
        }
    }
    matches!(params.agent_type.as_str(), "codex" | "claude")
}

fn build_codex_argv(params: &LaunchParams) -> Vec<String> {
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

fn build_claude_argv(params: &LaunchParams) -> Vec<String> {
    let mut args: Vec<String> = vec![
        String::from("claude"),
        String::from("--dangerously-skip-permissions"),
    ];
    if let Some(model) = params.model.as_ref() {
        if !model.is_empty() {
            args.push(String::from("--model"));
            args.push(model.clone());
        }
    }
    // Same rationale as 'build_codex_argv': the prompt is sent as a
    // message into the running agent (not as a positional argv) so the
    // session stays alive past task completion and can be re-prompted.
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
        fs::read_to_string(&config_path).with_context(|| {
            format!("reading codex config: {}", config_path.display())
        })?
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
        fs::write(&config_path, output).with_context(|| {
            format!("writing codex config: {}", config_path.display())
        })?;
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
    args.into_iter().map(shell_quote).collect::<Vec<_>>().join(" ")
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

fn session_launch(
    conn: &Connection,
    config: &Config,
    params: LaunchParams,
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
    let command_line = resolve_launch_command(&params)?;
    if !params.skip_trust_setup {
        run_pre_launch_setup(&params)?;
    }
    let pane_id = tmux_new_session(
        config,
        &params.session_name,
        &params.work_dir,
        &params.session_env,
    )?;
    if !command_line.trim().is_empty() {
        tmux_send_keys(config, &pane_id, &command_line, true, true)?;
    }
    // For interactive agents (codex / claude), the prompt is sent as a
    // message INTO the running agent's REPL — not as a positional argv —
    // so the session survives task completion and the monitor can
    // re-prompt the still-alive agent when the output contract is
    // violated.  Agents launched via an explicit `command` keep the
    // legacy behaviour: callers that hand over their own argv are
    // responsible for including a prompt if they want one.
    let mut awaiting_response = false;
    if uses_interactive_prompt(&params) {
        if let Some(prompt) = params.prompt.as_ref() {
            if !prompt.trim().is_empty() {
                // Wait for the agent's REPL to actually be ready for
                // input before sending the prompt + Enter.  Codex (and
                // similar) print their banner, load MCP servers, and
                // only then enter the input loop.  Sending keys before
                // that race lets the text queue up while the Enter is
                // eaten by the loading screen — the visible symptom
                // was a prompt sitting in codex's input box that was
                // never submitted.
                wait_for_repl_idle(config, &pane_id, Duration::from_secs(20))?;
                tmux_send_keys(config, &pane_id, prompt, true, true)?;
                awaiting_response = true;
            }
        }
    }
    let mut backend_ref = BTreeMap::new();
    backend_ref.insert(String::from("session_name"), params.session_name.clone());
    backend_ref.insert(String::from("pane_id"), pane_id.clone());
    backend_ref.insert(String::from("command"), command_line.clone());
    let started_at = now_iso();
    let snapshot = SessionSnapshot {
        session_id: params.session_id.clone(),
        session_name: params.session_name,
        pane_id,
        agent_type: params.agent_type,
        work_dir: params.work_dir,
        lifecycle: params.lifecycle,
        status: String::from("booting"),
        backend_kind: String::from("tmux"),
        backend_ref,
        started_at,
        last_observed_at: None,
        finished_at: None,
        cleaned_at: None,
        pr_url: None,
        output_snippet: None,
        expected_result: params.expected_result,
        retries_used: 0,
        last_validation_error: None,
        awaiting_response,
    };
    record_command(
        conn,
        Some(&snapshot.session_id),
        "session.launch",
        "completed",
        None,
        &snapshot,
    )?;
    upsert_snapshot(conn, &snapshot)?;
    record_event(conn, &snapshot.session_id, "session_started", &snapshot)?;
    Ok(snapshot)
}

fn session_get(conn: &Connection, session_id: &str) -> Result<Option<SessionSnapshot>> {
    conn.query_row(
        "SELECT session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status,
                backend_kind, backend_ref_json, started_at, last_observed_at,
                finished_at, cleaned_at, pr_url, output_snippet,
                expected_result_json, retries_used, last_validation_error,
                awaiting_response
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
                expected_result_json, retries_used, last_validation_error,
                awaiting_response
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
    snapshot.last_observed_at = Some(now);
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

fn require_session(conn: &Connection, session_id: &str) -> Result<SessionSnapshot> {
    session_get(conn, session_id)?.ok_or_else(|| anyhow!("session is not registered: {session_id}"))
}

fn row_to_snapshot(row: &rusqlite::Row<'_>) -> rusqlite::Result<SessionSnapshot> {
    let backend_ref_json: String = row.get(8)?;
    let backend_ref =
        serde_json::from_str::<BTreeMap<String, String>>(&backend_ref_json).map_err(|err| {
            rusqlite::Error::FromSqlConversionFailure(8, rusqlite::types::Type::Text, Box::new(err))
        })?;
    let expected_result_json: Option<String> = row.get(15)?;
    let expected_result = match expected_result_json {
        Some(json) => Some(serde_json::from_str::<ExpectedResultSpec>(&json).map_err(|err| {
            rusqlite::Error::FromSqlConversionFailure(
                15,
                rusqlite::types::Type::Text,
                Box::new(err),
            )
        })?),
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
        expected_result,
        retries_used: row.get::<_, i64>(16)? as u32,
        last_validation_error: row.get(17)?,
        awaiting_response: row.get::<_, i64>(18)? != 0,
    })
}

fn upsert_snapshot(conn: &Connection, snapshot: &SessionSnapshot) -> Result<()> {
    let backend_ref_json = serde_json::to_string(&snapshot.backend_ref)?;
    let expected_result_json = match &snapshot.expected_result {
        Some(spec) => Some(serde_json::to_string(spec)?),
        None => None,
    };
    conn.execute(
        "INSERT INTO agent_sessions (
            session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status,
            backend_kind, backend_ref_json, started_at, last_observed_at,
            finished_at, cleaned_at, pr_url, output_snippet,
            expected_result_json, retries_used, last_validation_error,
            awaiting_response
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)
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
            expected_result_json = excluded.expected_result_json,
            retries_used = excluded.retries_used,
            last_validation_error = excluded.last_validation_error,
            awaiting_response = excluded.awaiting_response",
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
            expected_result_json,
            i64::from(snapshot.retries_used),
            snapshot.last_validation_error,
            i64::from(snapshot.awaiting_response),
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

fn tmux_new_session(
    config: &Config,
    session_name: &str,
    work_dir: &str,
    env_vars: &BTreeMap<String, String>,
) -> Result<String> {
    let mut command = Command::new(&config.tmux_bin);
    command.args(["new-session", "-d", "-s", session_name, "-P", "-F", "#D"]);
    command.args(["-c", work_dir]);
    for (key, value) in env_vars {
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
    if enter {
        // codex renders the input box character-by-character; if we
        // press Enter the same millisecond the last byte of the
        // prompt lands, the keystroke can arrive while the UI is
        // still in a transient state and get silently dropped, leaving
        // the text sitting in the input forever.  A short pause gives
        // codex time to settle into the "prompt ready, awaiting
        // submit" state before the Enter is delivered.
        thread::sleep(Duration::from_millis(200));
        let enter_status = Command::new(&config.tmux_bin)
            .args(["send-keys", "-t", target, "Enter"])
            .status()
            .context("tmux send Enter failed to run")?;
        if !enter_status.success() {
            return Err(anyhow!("tmux send Enter failed"));
        }
    }
    Ok(())
}

fn tmux_capture(config: &Config, target: &str, lines: i64) -> Result<String> {
    let start = format!("-{}", lines.max(1));
    let output = Command::new(&config.tmux_bin)
        .args(["capture-pane", "-t", target, "-p", "-S", &start])
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
/// * @running@ — anything else, including codex's per-turn "Worked
///   for X" status display.  That marker is a *turn-end* signal, not
///   a work-end signal: see 'output_indicates_turn_end' and the
///   monitor's contract-validation block.
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
    let idle = output_has_codex_idle_prompt(output)
        && !output_has_codex_active_marker(output);
    let stable = output_is_stable(snapshot, output);
    idle && stable
}

fn should_cleanup_after_observed_status(snapshot: &SessionSnapshot, status: &str) -> bool {
    is_run_to_completion_lifecycle(&snapshot.lifecycle) && (status == "done" || status == "failed")
}

fn is_terminal_status(status: &str) -> bool {
    status == "done" || status == "failed" || status == "exited" || status == "stopped"
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

fn output_has_codex_idle_prompt(output: &str) -> bool {
    output.starts_with("› ") || output.contains("\n› ")
}

fn output_has_codex_active_marker(output: &str) -> bool {
    let text = output_tail_lower(output, 30);
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

fn monitor_loop(config: Config) {
    loop {
        if let Err(err) = monitor_once(&config) {
            eprintln!("doeff-agentd monitor error: {err:#}");
        }
        thread::sleep(config.monitor_interval);
    }
}

fn heartbeat_loop(config: Config) {
    let interval = Duration::from_secs((LEASE_TTL_SECONDS as u64 / 3).max(1));
    loop {
        if let Err(err) = heartbeat_once(&config) {
            eprintln!("doeff-agentd heartbeat error: {err:#}");
        }
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
            if age > ChronoDuration::seconds(STALE_OBSERVATION_THRESHOLD_SECONDS) {
                snapshot.status = String::from("exited");
                let observed = now_iso();
                snapshot.last_observed_at = Some(observed.clone());
                snapshot.finished_at.get_or_insert(observed);
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
        let exists = tmux_has_session(config, &snapshot.session_name)?;
        let observed_at = now_iso();
        if exists {
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
                if let Some(current_command) =
                    tmux_pane_current_command(config, &snapshot.pane_id)?
                {
                    if pane_looks_like_idle_shell(&current_command) {
                        snapshot.status = String::from("exited");
                        snapshot.last_observed_at = Some(observed_at.clone());
                        snapshot.finished_at.get_or_insert_with(now_iso);
                        upsert_snapshot(&conn, &snapshot)?;
                        record_event(
                            &conn,
                            &snapshot.session_id,
                            "session_exited",
                            &snapshot,
                        )?;
                        continue;
                    }
                }
            }
            let output = tmux_capture(config, &snapshot.pane_id, 100)?;
            // First, clear the awaiting-response latch once we see the
            // agent's "active" marker — that confirms the prompt landed
            // in the REPL and the agent is actually working on it.
            if snapshot.awaiting_response && output_has_codex_active_marker(&output) {
                snapshot.awaiting_response = false;
            }
            let raw_status = observed_status_for_snapshot(&snapshot, &output);
            snapshot.last_observed_at = Some(observed_at);

            // Turn-end is the agent's "I finished one ply, what's
            // next" signal.  We use it for two things:
            //
            //  * Kind 2 (RunToCompletion): trigger contract
            //    validation.  Until the contract passes the work is
            //    not done — we either retry or fail.
            //  * Kind 1 (Interactive): nothing.  The session just
            //    sits at the idle prompt awaiting the next user
            //    input; cleanup is the client's responsibility.
            //
            // 'awaiting_response' is the latch that ignores turn-end
            // events between the moment we inject a retry prompt and
            // the moment the agent visibly picks it up; that prevents
            // us re-validating against the same "Worked for" line
            // we already reacted to one cycle earlier.
            //
            // CRITICAL: 'output_indicates_turn_end' compares the
            // current output against 'snapshot.output_snippet' to
            // decide stability.  We must therefore evaluate it
            // BEFORE writing the fresh snippet back into the
            // snapshot, otherwise the comparison degenerates into
            // "current == current" and every observation looks
            // stable, firing the turn-end branch prematurely.
            let turn_ended =
                !snapshot.awaiting_response && output_indicates_turn_end(&snapshot, &output);
            snapshot.output_snippet = Some(tail_chars(&output, 500));

            let mut observed_status = raw_status;

            if turn_ended && is_run_to_completion_lifecycle(&snapshot.lifecycle) {
                match snapshot.expected_result.clone() {
                    Some(spec) => match validate_expected_result(&snapshot.work_dir, &spec) {
                        Ok(()) => {
                            observed_status = "done";
                            snapshot.last_validation_error = None;
                        }
                        Err(reason) => {
                            if snapshot.retries_used < spec.max_retries {
                                send_retry_prompt(config, &snapshot, &spec, &reason)?;
                                snapshot.retries_used += 1;
                                snapshot.last_validation_error = Some(reason.clone());
                                snapshot.status = String::from("running");
                                snapshot.finished_at = None;
                                snapshot.awaiting_response = true;
                                upsert_snapshot(&conn, &snapshot)?;
                                record_event(
                                    &conn,
                                    &snapshot.session_id,
                                    "session_output_retry",
                                    &snapshot,
                                )?;
                                continue;
                            } else {
                                observed_status = "failed";
                                snapshot.last_validation_error = Some(format!(
                                    "output validation exhausted after {} retries: {}",
                                    spec.max_retries, reason
                                ));
                            }
                        }
                    },
                    None => {
                        // RunToCompletion without an explicit contract
                        // means the launcher trusts the turn-end signal
                        // as work-end.  Mark done and let cleanup run.
                        observed_status = "done";
                    }
                }
            }

            snapshot.status = String::from(observed_status);
            if is_terminal_status(observed_status) {
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
            snapshot.status = String::from("exited");
            snapshot.last_observed_at = Some(observed_at.clone());
            snapshot.finished_at = Some(observed_at);
            upsert_snapshot(&conn, &snapshot)?;
            record_event(&conn, &snapshot.session_id, "session_exited", &snapshot)?;
        }
    }
    Ok(())
}

/// Validate the file the launcher promised the agent would produce.
/// Returns `Ok(())` when the file exists, parses as JSON, and matches
/// the declared schema name / version (when those are configured).
/// Otherwise the `Err(String)` carries a one-line explanation suitable
/// for inclusion in the retry prompt and in the session's last
/// `validation_error` audit field.
fn validate_expected_result(
    work_dir: &str,
    spec: &ExpectedResultSpec,
) -> std::result::Result<(), String> {
    let path = Path::new(work_dir).join(&spec.file_path);
    let raw = match fs::read_to_string(&path) {
        Ok(contents) => contents,
        Err(err) => {
            return Err(format!(
                "expected result file not readable at {}: {}",
                path.display(),
                err
            ));
        }
    };
    let value: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(err) => {
            return Err(format!(
                "expected result file at {} is not valid JSON: {}",
                path.display(),
                err
            ));
        }
    };
    if let Some(expected_name) = &spec.schema_name {
        let actual = value
            .get("schema")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if actual != expected_name.as_str() {
            return Err(format!(
                "schema mismatch in {}: expected '{}', got '{}'",
                path.display(),
                expected_name,
                actual
            ));
        }
    }
    if let Some(expected_version) = spec.schema_version {
        let actual = value
            .get("schemaVersion")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        if actual != u64::from(expected_version) {
            return Err(format!(
                "schemaVersion mismatch in {}: expected {}, got {}",
                path.display(),
                expected_version,
                actual
            ));
        }
    }
    Ok(())
}

/// Send the validator's feedback into the still-alive agent session as
/// the next message in its REPL.  Crucially we do NOT re-run a fresh
/// agent invocation — the agent process is the same one that just
/// finished the task, so it has the full conversation context and can
/// act on the feedback the way a human user would by typing follow-up
/// instructions.  The prompt template's `%REASON%` placeholder is
/// substituted with the validator's explanation so the agent knows
/// what to fix.
///
/// Supported agents are the ones that own an interactive REPL we can
/// drive via tmux keystrokes; for everything else we cannot auto-retry
/// reliably and the caller surfaces the failure instead.
fn send_retry_prompt(
    config: &Config,
    snapshot: &SessionSnapshot,
    spec: &ExpectedResultSpec,
    reason: &str,
) -> Result<()> {
    if !is_interactive_agent_type(&snapshot.agent_type) {
        return Err(anyhow!(
            "cannot auto-retry agent_type '{}': only codex and claude are supported",
            snapshot.agent_type
        ));
    }
    // Make sure codex is parked at the idle prompt before we type
    // anything — otherwise the message lands in an unrelated UI
    // state (banner, MCP loader, modal) and the Enter that follows
    // can be eaten.  We tolerate a missing idle marker because the
    // caller already concluded the agent reached turn-end; this
    // poll just absorbs the small window between turn-end detection
    // and the input box becoming receptive.
    wait_for_repl_idle(config, &snapshot.pane_id, Duration::from_secs(5))?;
    let prompt = spec.retry_prompt.replace("%REASON%", reason);
    tmux_send_keys(config, &snapshot.pane_id, &prompt, true, true)?;
    Ok(())
}

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
/// appears or after `max_wait`, whichever comes first; the caller is
/// expected to send the keys regardless so a stuck startup at least
/// fails the normal validation path instead of hanging the RPC.
fn wait_for_repl_idle(config: &Config, pane_id: &str, max_wait: Duration) -> Result<()> {
    let start = std::time::Instant::now();
    let poll_interval = Duration::from_millis(300);
    while start.elapsed() < max_wait {
        let output = tmux_capture(config, pane_id, 60)?;
        if output_has_codex_idle_prompt(&output) {
            return Ok(());
        }
        thread::sleep(poll_interval);
    }
    Ok(())
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
    use std::path::Path;

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
            expected_result: None,
            retries_used: 0,
            last_validation_error: None,
            awaiting_response: false,
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
                last_observed_at: None,
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
            },
        )
        .expect("insert active session");
        let config = Config {
            db_path: db,
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin: String::from("tmux"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 1,
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
                last_observed_at: Some(stale_iso),
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
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
                last_observed_at: Some(fresh_iso),
                finished_at: None,
                cleaned_at: None,
                pr_url: None,
                output_snippet: None,
            },
        )
        .expect("insert fresh session");
        let config = Config {
            db_path: db.clone(),
            socket_path: tmp.path().join("agentd.sock"),
            tmux_bin: String::from("tmux"),
            monitor_interval: Duration::from_millis(1000),
            max_running: 10,
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
    fn validate_expected_result_accepts_well_formed_envelope() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let work_dir = tmp.path().to_path_buf();
        fs::write(
            work_dir.join(".reactor-impl-result.json"),
            r#"{"schema":"ImplResult","schemaVersion":1,"payload":{"pr_url":"https://x"}}"#,
        )
        .expect("write envelope");
        let spec = ExpectedResultSpec {
            file_path: String::from(".reactor-impl-result.json"),
            schema_name: Some(String::from("ImplResult")),
            schema_version: Some(1),
            retry_prompt: default_retry_prompt(),
            max_retries: 2,
        };
        assert!(validate_expected_result(work_dir.to_str().unwrap(), &spec).is_ok());
    }

    #[test]
    fn validate_expected_result_rejects_missing_file() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let spec = ExpectedResultSpec {
            file_path: String::from(".reactor-impl-result.json"),
            schema_name: None,
            schema_version: None,
            retry_prompt: default_retry_prompt(),
            max_retries: 2,
        };
        let err = validate_expected_result(tmp.path().to_str().unwrap(), &spec)
            .expect_err("missing file should reject");
        assert!(err.contains("not readable"), "got: {err}");
    }

    #[test]
    fn validate_expected_result_rejects_schema_mismatch() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let work_dir = tmp.path().to_path_buf();
        fs::write(
            work_dir.join(".reactor-impl-result.json"),
            r#"{"schema":"WrongSchema","schemaVersion":1}"#,
        )
        .expect("write envelope");
        let spec = ExpectedResultSpec {
            file_path: String::from(".reactor-impl-result.json"),
            schema_name: Some(String::from("ImplResult")),
            schema_version: Some(1),
            retry_prompt: default_retry_prompt(),
            max_retries: 2,
        };
        let err = validate_expected_result(work_dir.to_str().unwrap(), &spec)
            .expect_err("schema mismatch should reject");
        assert!(err.contains("schema mismatch"), "got: {err}");
    }

    #[test]
    fn validate_expected_result_rejects_schema_version_mismatch() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let work_dir = tmp.path().to_path_buf();
        fs::write(
            work_dir.join(".reactor-impl-result.json"),
            r#"{"schema":"ImplResult","schemaVersion":2}"#,
        )
        .expect("write envelope");
        let spec = ExpectedResultSpec {
            file_path: String::from(".reactor-impl-result.json"),
            schema_name: Some(String::from("ImplResult")),
            schema_version: Some(1),
            retry_prompt: default_retry_prompt(),
            max_retries: 2,
        };
        let err = validate_expected_result(work_dir.to_str().unwrap(), &spec)
            .expect_err("schema version mismatch should reject");
        assert!(err.contains("schemaVersion mismatch"), "got: {err}");
    }

    #[test]
    fn interactive_agent_types_are_codex_and_claude() {
        assert!(is_interactive_agent_type("codex"));
        assert!(is_interactive_agent_type("claude"));
        assert!(!is_interactive_agent_type("generic"));
        assert!(!is_interactive_agent_type(""));
    }

    #[test]
    fn build_codex_argv_does_not_include_prompt_as_argument() {
        let params = LaunchParams {
            session_id: String::from("s"),
            session_name: String::from("s"),
            agent_type: String::from("codex"),
            work_dir: String::from("/tmp"),
            command: None,
            prompt: Some(String::from("hello world")),
            model: None,
            effort: None,
            mcp_servers: BTreeMap::new(),
            skip_trust_setup: true,
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            session_env: BTreeMap::new(),
            expected_result: None,
        };
        let argv = build_codex_argv(&params);
        assert!(
            !argv.iter().any(|arg| arg == "hello world"),
            "prompt must not appear in codex argv (it is sent via send-keys instead): {argv:?}"
        );
        assert_eq!(argv[0], "codex");
        assert!(argv.contains(&String::from("--yolo")));
    }

    #[test]
    fn uses_interactive_prompt_is_true_for_codex_without_command() {
        let params = LaunchParams {
            session_id: String::from("s"),
            session_name: String::from("s"),
            agent_type: String::from("codex"),
            work_dir: String::from("/tmp"),
            command: None,
            prompt: Some(String::from("p")),
            model: None,
            effort: None,
            mcp_servers: BTreeMap::new(),
            skip_trust_setup: true,
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            session_env: BTreeMap::new(),
            expected_result: None,
        };
        assert!(uses_interactive_prompt(&params));
    }

    #[test]
    fn uses_interactive_prompt_is_false_for_explicit_command_override() {
        let params = LaunchParams {
            session_id: String::from("s"),
            session_name: String::from("s"),
            agent_type: String::from("codex"),
            work_dir: String::from("/tmp"),
            command: Some(String::from("./run.sh")),
            prompt: Some(String::from("p")),
            model: None,
            effort: None,
            mcp_servers: BTreeMap::new(),
            skip_trust_setup: true,
            lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
            session_env: BTreeMap::new(),
            expected_result: None,
        };
        assert!(!uses_interactive_prompt(&params));
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
            expected_result: None,
            retries_used: 0,
            last_validation_error: None,
            awaiting_response: false,
        }
    }
}
