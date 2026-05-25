# doeff-agentd Rust daemon plan

## 10 second summary

`doeff-agents` should stop owning long-lived agent lifecycle inside a short-lived
Python handler process.  Add a Rust `doeff-agentd` supervisor that owns tmux
sessions and a SQLite read model; make the Python effect handler a daemon client.

MCP lifecycle stays outside this daemon.  `defmcp` prepares tool wiring and any
workdir files before launch.

## Problem

The current production handler mixes two different jobs:

- lifecycle ownership: launch, stop, cleanup, and backend mutation
- client observation: list, get, capture, and UI-facing reads

This makes read-like effects such as `ObserveAgentSession` update backend state.
It also means Python process restarts can blur who owns a session that outlives
that process.

## Target architecture

```text
doeff Program
  -> Python doeff_agents handler
    -> doeff-agentd client
      -> Rust doeff-agentd
        -> SQLite session store
        -> tmux/process backend
        -> monitor loop
```

## Ownership boundary

`doeff-agentd` owns:

- session launch, send, cancel, cleanup
- tmux/process backend operations
- SQLite session events and current session rows
- background monitor loop
- orphan/stale reconciliation
- max-running admission guard

`doeff-agentd` does not own:

- MCP server lifecycle
- `defmcp` tool dispatch
- issue, PR, workflow, or project semantics
- workspace creation policy beyond using the supplied `work_dir`
- adapter prompt construction beyond executing the supplied launch command

## Protocol

Use Unix domain socket JSON-line RPC first.  It is easy to call from Python
without adding a heavy dependency.  When paths are omitted, the daemon uses
XDG-style defaults:

- DB: `${XDG_STATE_HOME:-~/.local/state}/doeff/agentd.sqlite`
- socket: `${XDG_RUNTIME_DIR}/doeff/agentd.sock`
- socket fallback when `XDG_RUNTIME_DIR` is absent: `/tmp/doeff-agentd-${USER}.sock`

Methods:

- `daemon.status`
- `session.launch`
- `session.get`
- `session.list`
- `session.capture`
- `session.send`
- `session.cancel`
- `session.cleanup`

Read-only methods:

- `session.get`
- `session.list`

Only daemon commands and the daemon monitor loop update persistent state.

## SQLite read model

Tables:

- `agent_sessions`: current state for fast queries
- `agent_session_events`: append-only lifecycle facts
- `agent_session_commands`: command audit trail
- `agent_daemon_lease`: single-supervisor ownership heartbeat

## Python effect client mapping

- `LaunchEffect` -> `session.launch`
- `GetAgentSessionEffect` -> `session.get`
- `ListAgentSessionsEffect` -> `session.list`
- `ObserveAgentSessionEffect` -> compatibility `session.get`
- `CaptureEffect` -> `session.capture`
- `SendEffect` -> `session.send`
- `StopEffect` -> `session.cancel`
- `CancelAgentSessionEffect` -> `session.cancel`
- `CleanupAgentSessionEffect` -> `session.cleanup`

`ObserveAgentSessionEffect` should be deprecated for production observation.
State advancement belongs to the daemon monitor loop.

## First implementation slice

1. Add a Rust `packages/doeff-agentd` crate with SQLite schema and JSON-line
   Unix socket server.
2. Add tmux-backed launch/send/capture/cancel/cleanup commands in the daemon.
3. Add max-running admission control at the daemon launch boundary.
4. Add daemon lease / heartbeat so double starts fail instead of stealing state.
5. Add Python `AgentdClient`, `ensure_agentd`, and `DaemonAgentHandler`.
6. Keep existing `TmuxAgentHandler` as fallback and test transport.
7. Add tests for client protocol and read-only `ObserveAgentSession` mapping.

## Follow-up work

- Move Reactor agent projection reads to the daemon read model.
- Add richer Codex/Claude runtime analyzers in Rust.
- Add project/profile-specific admission-control policy backed by SQLite.
- Deprecate direct production use of `TmuxAgentHandler`.
