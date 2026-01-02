//! Fast CLI for doeff-agentic workflow management.
//!
//! This Rust CLI reads state files directly for fast plugin consumption.
//! It provides ~5ms startup time compared to ~300ms for Python.
//!
//! Commands:
//!   ps          List running workflows
//!   show        Show workflow details
//!   watch       Stream workflow updates (JSONL)
//!   attach      Attach to agent's tmux session
//!   send        Send message to agent
//!   stop        Stop workflow

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::thread;
use std::time::Duration;
use tabled::{Table, Tabled};

/// Fast CLI for doeff-agentic workflow management
#[derive(Parser)]
#[command(name = "doeff-agentic")]
#[command(about = "Agent-based workflow orchestration", long_about = None)]
struct Cli {
    /// State directory (defaults to ~/.local/state/doeff-agentic)
    #[arg(long, env = "DOEFF_AGENTIC_STATE_DIR")]
    state_dir: Option<PathBuf>,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// List running workflows and agents
    Ps {
        /// Filter by workflow status
        #[arg(long, short)]
        status: Option<Vec<String>>,

        /// Filter by agent status
        #[arg(long, short = 'a')]
        agent_status: Option<Vec<String>>,

        /// Output as JSON
        #[arg(long)]
        json: bool,
    },

    /// Show workflow details
    Show {
        /// Workflow ID or prefix
        workflow_id: String,

        /// Output as JSON
        #[arg(long)]
        json: bool,
    },

    /// Watch workflow updates (streams JSONL)
    Watch {
        /// Workflow ID or prefix
        workflow_id: String,

        /// Output as JSON (JSONL stream)
        #[arg(long)]
        json: bool,

        /// Poll interval in seconds
        #[arg(long, short, default_value = "1.0")]
        poll: f64,
    },

    /// Attach to agent's tmux session
    Attach {
        /// Workflow ID or prefix
        workflow_id: String,

        /// Specific agent name
        #[arg(long, short)]
        agent: Option<String>,
    },

    /// Send message to running agent
    Send {
        /// Workflow ID or prefix
        workflow_id: String,

        /// Message to send
        message: String,

        /// Specific agent name
        #[arg(long, short)]
        agent: Option<String>,

        /// Output as JSON
        #[arg(long)]
        json: bool,
    },

    /// Stop workflow and kill agents
    Stop {
        /// Workflow ID or prefix
        workflow_id: String,

        /// Output as JSON
        #[arg(long)]
        json: bool,
    },
}

// =============================================================================
// Data Types
// =============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
struct WorkflowMeta {
    id: String,
    name: String,
    status: String,
    started_at: String,
    updated_at: String,
    current_agent: Option<String>,
    last_slog: Option<serde_json::Value>,
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AgentState {
    name: String,
    status: String,
    session_name: String,
    pane_id: Option<String>,
    started_at: String,
    last_output_hash: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct WorkflowInfo {
    id: String,
    name: String,
    status: String,
    started_at: String,
    updated_at: String,
    current_agent: Option<String>,
    agents: Vec<AgentState>,
    last_slog: Option<serde_json::Value>,
    error: Option<String>,
}

#[derive(Tabled)]
struct WorkflowRow {
    #[tabled(rename = "ID")]
    id: String,
    #[tabled(rename = "Workflow")]
    name: String,
    #[tabled(rename = "Status")]
    status: String,
    #[tabled(rename = "Agent")]
    agent: String,
    #[tabled(rename = "Agent Status")]
    agent_status: String,
    #[tabled(rename = "Updated")]
    updated: String,
}

// =============================================================================
// State Manager
// =============================================================================

struct StateManager {
    state_dir: PathBuf,
}

impl StateManager {
    fn new(state_dir: Option<PathBuf>) -> Self {
        let dir = state_dir.unwrap_or_else(|| {
            dirs::state_dir()
                .unwrap_or_else(|| dirs::home_dir().unwrap().join(".local/state"))
                .join("doeff-agentic")
        });
        Self { state_dir: dir }
    }

    fn load_index(&self) -> Result<HashMap<String, String>> {
        let index_path = self.state_dir.join("index.json");
        if !index_path.exists() {
            return Ok(HashMap::new());
        }
        let content = fs::read_to_string(&index_path)
            .context("Failed to read index.json")?;
        let index: HashMap<String, String> = serde_json::from_str(&content)
            .context("Failed to parse index.json")?;
        Ok(index)
    }

    fn resolve_prefix(&self, prefix: &str) -> Result<Option<String>> {
        let index = self.load_index()?;

        // Exact match
        if index.contains_key(prefix) {
            return Ok(Some(prefix.to_string()));
        }

        // Prefix match
        let matches: Vec<_> = index
            .keys()
            .filter(|k| k.starts_with(prefix))
            .cloned()
            .collect();

        match matches.len() {
            0 => Ok(None),
            1 => Ok(Some(matches[0].clone())),
            _ => {
                let details: Vec<_> = matches
                    .iter()
                    .map(|id| format!("{} ({})", id, index.get(id).unwrap_or(&"?".to_string())))
                    .collect();
                anyhow::bail!(
                    "Ambiguous prefix '{}' matches multiple workflows: {}",
                    prefix,
                    details.join(", ")
                );
            }
        }
    }

    fn read_workflow(&self, workflow_id: &str) -> Result<Option<WorkflowInfo>> {
        let full_id = match self.resolve_prefix(workflow_id)? {
            Some(id) => id,
            None => return Ok(None),
        };

        let workflow_dir = self.state_dir.join("workflows").join(&full_id);
        let meta_path = workflow_dir.join("meta.json");

        if !meta_path.exists() {
            return Ok(None);
        }

        let meta_content = fs::read_to_string(&meta_path)
            .context("Failed to read meta.json")?;
        let meta: WorkflowMeta = serde_json::from_str(&meta_content)
            .context("Failed to parse meta.json")?;

        // Load agents
        let agents_dir = workflow_dir.join("agents");
        let mut agents = Vec::new();

        if agents_dir.exists() {
            for entry in fs::read_dir(&agents_dir)? {
                let entry = entry?;
                let path = entry.path();
                if path.extension().map(|e| e == "json").unwrap_or(false) {
                    let content = fs::read_to_string(&path)?;
                    if let Ok(agent) = serde_json::from_str::<AgentState>(&content) {
                        agents.append(&mut vec![agent]);
                    }
                }
            }
        }

        Ok(Some(WorkflowInfo {
            id: meta.id,
            name: meta.name,
            status: meta.status,
            started_at: meta.started_at,
            updated_at: meta.updated_at,
            current_agent: meta.current_agent,
            agents,
            last_slog: meta.last_slog,
            error: meta.error,
        }))
    }

    fn list_workflows(&self) -> Result<Vec<WorkflowInfo>> {
        let workflows_dir = self.state_dir.join("workflows");
        if !workflows_dir.exists() {
            return Ok(Vec::new());
        }

        let mut workflows = Vec::new();
        for entry in fs::read_dir(&workflows_dir)? {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                let id = entry.file_name().to_string_lossy().to_string();
                if let Some(wf) = self.read_workflow(&id)? {
                    workflows.push(wf);
                }
            }
        }

        // Sort by updated_at descending
        workflows.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));

        Ok(workflows)
    }
}

// =============================================================================
// Commands
// =============================================================================

fn cmd_ps(
    state: &StateManager,
    status_filter: Option<Vec<String>>,
    agent_status_filter: Option<Vec<String>>,
    json: bool,
) -> Result<()> {
    let mut workflows = state.list_workflows()?;

    // Apply status filter
    if let Some(statuses) = &status_filter {
        workflows.retain(|wf| statuses.contains(&wf.status));
    }

    // Apply agent status filter
    if let Some(statuses) = &agent_status_filter {
        workflows.retain(|wf| {
            wf.agents.iter().any(|a| statuses.contains(&a.status))
        });
    }

    if json {
        println!("{}", serde_json::to_string_pretty(&workflows)?);
        return Ok(());
    }

    if workflows.is_empty() {
        println!("No workflows found");
        return Ok(());
    }

    let rows: Vec<WorkflowRow> = workflows
        .iter()
        .map(|wf| {
            let (agent, agent_status) = if let Some(ref current) = wf.current_agent {
                let status = wf
                    .agents
                    .iter()
                    .find(|a| &a.name == current)
                    .map(|a| a.status.clone())
                    .unwrap_or_else(|| "-".to_string());
                (current.clone(), status)
            } else {
                ("-".to_string(), "-".to_string())
            };

            WorkflowRow {
                id: wf.id.clone(),
                name: wf.name.clone(),
                status: wf.status.clone(),
                agent,
                agent_status,
                updated: format_duration(&wf.updated_at),
            }
        })
        .collect();

    let table = Table::new(rows).to_string();
    println!("{}", table);

    Ok(())
}

fn cmd_show(state: &StateManager, workflow_id: &str, json: bool) -> Result<()> {
    let workflow = state
        .read_workflow(workflow_id)?
        .ok_or_else(|| anyhow::anyhow!("Workflow not found: {}", workflow_id))?;

    if json {
        println!("{}", serde_json::to_string_pretty(&workflow)?);
    } else {
        println!("ID: {}", workflow.id);
        println!("Name: {}", workflow.name);
        println!("Status: {}", workflow.status);
        println!("Started: {}", workflow.started_at);
        println!("Updated: {}", workflow.updated_at);

        if let Some(ref agent) = workflow.current_agent {
            println!("Current Agent: {}", agent);
        }

        if !workflow.agents.is_empty() {
            println!("\nAgents:");
            for a in &workflow.agents {
                let marker = if Some(&a.name) == workflow.current_agent.as_ref() {
                    "*"
                } else {
                    " "
                };
                println!("  {} {}: {} ({})", marker, a.name, a.status, a.session_name);
            }
        }

        if let Some(ref slog) = workflow.last_slog {
            println!("\nLast Status: {}", serde_json::to_string_pretty(slog)?);
        }

        if let Some(ref error) = workflow.error {
            println!("\nError: {}", error);
        }
    }

    Ok(())
}

fn cmd_watch(state: &StateManager, workflow_id: &str, json: bool, poll: f64) -> Result<()> {
    let full_id = state
        .resolve_prefix(workflow_id)?
        .ok_or_else(|| anyhow::anyhow!("Workflow not found: {}", workflow_id))?;

    let poll_duration = Duration::from_secs_f64(poll);
    let mut last_updated: Option<String> = None;

    loop {
        let workflow = match state.read_workflow(&full_id)? {
            Some(wf) => wf,
            None => {
                if json {
                    println!(
                        "{}",
                        serde_json::json!({"event": "deleted", "workflow_id": full_id})
                    );
                } else {
                    println!("Workflow deleted");
                }
                break;
            }
        };

        // Check for change
        let changed = last_updated
            .as_ref()
            .map(|last| last != &workflow.updated_at)
            .unwrap_or(true);

        if changed {
            if json {
                println!("{}", serde_json::to_string(&workflow)?);
            } else {
                // Clear screen and print status
                print!("\x1B[2J\x1B[1;1H");
                println!("Watching: {} ({})", workflow.id, workflow.name);
                println!("Status: {}", workflow.status);
                println!("Updated: {}", format_duration(&workflow.updated_at));

                if let Some(ref agent) = workflow.current_agent {
                    println!("Current Agent: {}", agent);
                }

                if let Some(ref slog) = workflow.last_slog {
                    println!("\nLast Status: {}", serde_json::to_string_pretty(slog)?);
                }

                println!("\n[Ctrl+C to stop]");
            }
            last_updated = Some(workflow.updated_at.clone());
        }

        // Check for terminal status
        if workflow.status == "completed" || workflow.status == "failed" {
            if json {
                println!(
                    "{}",
                    serde_json::json!({"event": "terminal", "status": workflow.status})
                );
            }
            break;
        }

        thread::sleep(poll_duration);
    }

    Ok(())
}

fn cmd_attach(state: &StateManager, workflow_id: &str, agent: Option<String>) -> Result<()> {
    let workflow = state
        .read_workflow(workflow_id)?
        .ok_or_else(|| anyhow::anyhow!("Workflow not found: {}", workflow_id))?;

    // Determine which agent to attach to
    let target_agent = agent
        .or_else(|| workflow.current_agent.clone())
        .or_else(|| workflow.agents.first().map(|a| a.name.clone()));

    let target_agent = target_agent.ok_or_else(|| anyhow::anyhow!("No agent to attach to"))?;

    // Find session name
    let session_name = workflow
        .agents
        .iter()
        .find(|a| a.name == target_agent)
        .map(|a| a.session_name.clone())
        .unwrap_or_else(|| format!("doeff-{}-{}", workflow.id, target_agent));

    // Check if in tmux
    let in_tmux = std::env::var("TMUX").is_ok();

    println!("Attaching to: {}", session_name);

    if in_tmux {
        // exec into tmux switch-client
        let err = Command::new("tmux")
            .args(["switch-client", "-t", &session_name])
            .exec();
        anyhow::bail!("Failed to exec tmux: {}", err);
    } else {
        // exec into tmux attach
        let err = Command::new("tmux")
            .args(["attach-session", "-t", &session_name])
            .exec();
        anyhow::bail!("Failed to exec tmux: {}", err);
    }
}

fn cmd_send(
    state: &StateManager,
    workflow_id: &str,
    message: &str,
    agent: Option<String>,
    json: bool,
) -> Result<()> {
    let workflow = state
        .read_workflow(workflow_id)?
        .ok_or_else(|| anyhow::anyhow!("Workflow not found: {}", workflow_id))?;

    // Check for input request file (WaitForUserInput)
    let input_request = state
        .state_dir
        .join("workflows")
        .join(&workflow.id)
        .join("input_request.json");

    if input_request.exists() {
        // This is a response to WaitForUserInput
        let response_file = state
            .state_dir
            .join("workflows")
            .join(&workflow.id)
            .join("input_response.txt");
        fs::write(&response_file, message)?;

        if json {
            println!("{}", serde_json::json!({"ok": true, "type": "user_input_response"}));
        } else {
            println!("Response sent");
        }
        return Ok(());
    }

    // Determine target agent
    let target_agent = agent
        .or_else(|| workflow.current_agent.clone())
        .ok_or_else(|| anyhow::anyhow!("No agent to send to"))?;

    // Find session/pane
    let target = workflow
        .agents
        .iter()
        .find(|a| a.name == target_agent)
        .and_then(|a| a.pane_id.clone().or(Some(a.session_name.clone())))
        .unwrap_or_else(|| format!("doeff-{}-{}", workflow.id, target_agent));

    // Send via tmux
    let result = Command::new("tmux")
        .args(["send-keys", "-t", &target, "-l", message, "Enter"])
        .status()?;

    let ok = result.success();

    if json {
        println!("{}", serde_json::json!({"ok": ok}));
    } else if ok {
        println!("Message sent");
    } else {
        anyhow::bail!("Failed to send message");
    }

    Ok(())
}

fn cmd_stop(state: &StateManager, workflow_id: &str, json: bool) -> Result<()> {
    let workflow = state
        .read_workflow(workflow_id)?
        .ok_or_else(|| anyhow::anyhow!("Workflow not found: {}", workflow_id))?;

    let mut stopped = Vec::new();

    for agent in &workflow.agents {
        let result = Command::new("tmux")
            .args(["kill-session", "-t", &agent.session_name])
            .status();

        if result.map(|s| s.success()).unwrap_or(false) {
            stopped.push(agent.name.clone());
        }
    }

    if json {
        println!("{}", serde_json::json!({"ok": true, "stopped": stopped}));
    } else if stopped.is_empty() {
        println!("No agents to stop");
    } else {
        println!("Stopped agents: {}", stopped.join(", "));
    }

    Ok(())
}

// =============================================================================
// Helpers
// =============================================================================

fn format_duration(iso_str: &str) -> String {
    let dt = match DateTime::parse_from_rfc3339(iso_str) {
        Ok(dt) => dt.with_timezone(&Utc),
        Err(_) => return iso_str.to_string(),
    };

    let now = Utc::now();
    let duration = now.signed_duration_since(dt);
    let seconds = duration.num_seconds();

    if seconds < 0 {
        return "in the future".to_string();
    }

    if seconds < 60 {
        format!("{}s ago", seconds)
    } else if seconds < 3600 {
        format!("{}m ago", seconds / 60)
    } else if seconds < 86400 {
        format!("{}h ago", seconds / 3600)
    } else {
        format!("{}d ago", seconds / 86400)
    }
}

// =============================================================================
// Main
// =============================================================================

fn main() -> Result<()> {
    let cli = Cli::parse();
    let state = StateManager::new(cli.state_dir);

    match cli.command {
        Commands::Ps {
            status,
            agent_status,
            json,
        } => cmd_ps(&state, status, agent_status, json),
        Commands::Show { workflow_id, json } => cmd_show(&state, &workflow_id, json),
        Commands::Watch {
            workflow_id,
            json,
            poll,
        } => cmd_watch(&state, &workflow_id, json, poll),
        Commands::Attach { workflow_id, agent } => cmd_attach(&state, &workflow_id, agent),
        Commands::Send {
            workflow_id,
            message,
            agent,
            json,
        } => cmd_send(&state, &workflow_id, &message, agent, json),
        Commands::Stop { workflow_id, json } => cmd_stop(&state, &workflow_id, json),
    }
}
