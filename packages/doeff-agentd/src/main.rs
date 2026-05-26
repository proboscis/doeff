use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
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

fn main() -> Result<()> {
    let config = parse_args(env::args().skip(1).collect())?;
    if let Some(parent) = config.db_path.parent() {
        fs::create_dir_all(parent)?;
    }
    if let Some(parent) = config.socket_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let conn = Connection::open(&config.db_path)?;
    migrate(&conn)?;
    acquire_lease(&conn)?;
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
    let conn = Connection::open(&config.db_path)?;
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
    if let Some(prompt) = params.prompt.as_ref() {
        if !prompt.is_empty() {
            args.push(prompt.clone());
        }
    }
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
    if let Some(prompt) = params.prompt.as_ref() {
        if !prompt.is_empty() {
            args.push(prompt.clone());
        }
    }
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
                finished_at, cleaned_at, pr_url, output_snippet
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
                finished_at, cleaned_at, pr_url, output_snippet
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
    })
}

fn upsert_snapshot(conn: &Connection, snapshot: &SessionSnapshot) -> Result<()> {
    let backend_ref_json = serde_json::to_string(&snapshot.backend_ref)?;
    conn.execute(
        "INSERT INTO agent_sessions (
            session_id, session_name, pane_id, agent_type, work_dir, lifecycle, status,
            backend_kind, backend_ref_json, started_at, last_observed_at,
            finished_at, cleaned_at, pr_url, output_snippet
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)
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
            output_snippet = excluded.output_snippet",
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

fn observed_status_for_snapshot(snapshot: &SessionSnapshot, output: &str) -> &'static str {
    if output_has_failure_marker(output) {
        return "failed";
    }
    if output_has_api_limit_marker(output) {
        return "blocked_api";
    }

    let completion = output_has_completion_marker(output);
    let idle_done = output_has_codex_idle_prompt(output)
        && !output_has_codex_active_marker(output)
        && output_is_stable(snapshot, output);
    if is_run_to_completion_lifecycle(&snapshot.lifecycle) && (completion || idle_done) {
        return "done";
    }
    if completion || idle_done || output_has_waiting_marker(output) {
        return "blocked";
    }
    "running"
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

fn output_has_completion_marker(output: &str) -> bool {
    let text = output_tail_lower(output, 30);
    text.contains("task completed successfully")
        || text.contains("all tasks completed")
        || text.contains("session ended")
        || text.contains("goodbye")
        || text.contains("worked for")
}

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
    text.contains("working (")
        || text.contains("thinking")
        || text.contains("esc to interrupt")
        || text.contains("ctrl + t to view transcript")
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
    let conn = Connection::open(&config.db_path)?;
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
    let conn = Connection::open(&config.db_path)?;
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
    for mut snapshot in active {
        let exists = tmux_has_session(config, &snapshot.session_name)?;
        let observed_at = now_iso();
        if exists {
            let output = tmux_capture(config, &snapshot.pane_id, 100)?;
            let observed_status = observed_status_for_snapshot(&snapshot, &output);
            snapshot.status = String::from(observed_status);
            snapshot.last_observed_at = Some(observed_at);
            snapshot.output_snippet = Some(tail_chars(&output, 500));
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
                command: String::from("true"),
                lifecycle: String::from(LIFECYCLE_RUN_TO_COMPLETION),
                session_env: BTreeMap::new(),
            },
        )
        .expect_err("max running should reject before tmux");
        assert!(err
            .to_string()
            .contains("max running agent sessions reached"));
    }

    #[test]
    fn run_to_completion_output_marks_session_done() {
        let snapshot = snapshot_for_lifecycle("run_to_completion", "running");
        let output = "task completed successfully\nworked for 1m 2s\n› ";

        let status = observed_status_for_snapshot(&snapshot, output);

        assert_eq!(status, "done");
        assert!(should_cleanup_after_observed_status(&snapshot, status));
    }

    #[test]
    fn interactive_output_remains_available_for_more_input() {
        let snapshot = snapshot_for_lifecycle("interactive", "running");
        let output = "task completed successfully\nworked for 1m 2s\n› ";

        let status = observed_status_for_snapshot(&snapshot, output);

        assert_eq!(status, "blocked");
        assert!(!should_cleanup_after_observed_status(&snapshot, status));
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
        }
    }
}
