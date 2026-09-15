# doeff-agents

Agent session management for coding agents (Claude, Codex, Gemini). Sessions run
on one of three substrates — a tmux terminal, a herdr terminal, or a headless
process — behind a single effect boundary.

## Installation

```bash
pip install doeff-agents
```

From a checkout of this repository, install the workspace instead (this also
rebuilds the Rust VM that `doeff` needs):

```bash
make sync
```

To check the install without touching any daemon:

```bash
doeff-agents --help
doeff-agents agentd --help
command -v doeff-sessionhost   # the host has no --help; see the note below
```

Those three need no host and no agent. To go further you need a running session
host: `doeff-agents agentd kinds` reports the binding-kind vocabulary of one,
and it is the only command that never starts a host of its own (see the table
below). With no host reachable it exits 1 and names the start command — after a
doeff effect traceback, which is noise, not a second failure.

Launching a real agent additionally needs an authenticated CLI on the machine.
The credential state is Claude Code's or Codex's own, and it reaches the host as
a *binding* built by the control plane; the host advertises the binding kinds it
accepts (`agentd kinds`) and a declaration naming an unknown kind or version is
refused. This package never accepts LLM provider API keys at the agent boundary
— see `AGENTS.md` (Agent Authentication Boundary) in the repository root.
Changing that refusal is a design question, tracked by the consuming
project's handover notes — not something to work around here.

## Using this from an agent control plane

Three pieces stack up: the control-plane **engine** schedules work, the
**client** (`doeff-agent-haskell`) talks to it, and **this package** is the host
that actually runs the agent on a machine. The host joins a cluster with
`doeff-sessionhost join` (below).

Which revisions of the three go together is declared by the consuming project,
typically in its `pyproject.toml` (for this package) plus a small pin file such
as `infra/provision/acp-revs.toml` (for the engine and the client). That
declaration is the source of truth; the pins are not copied here.

## Quick Start

### Python API

```python
import time
from pathlib import Path

from doeff_agents import (
    session_scope, monitor_session, send_message,
    LaunchConfig, AgentType, SessionStatus
)

config = LaunchConfig(
    agent_type=AgentType.CLAUDE,
    work_dir=Path("/path/to/project"),
    prompt="Read the README and summarize the project",
)

# Context manager ensures cleanup on exit/exception
with session_scope("my-agent-session", config) as session:
    while not session.is_terminal:
        new_status = monitor_session(session)
        if new_status:
            print(f"Status changed to: {new_status}")

        if session.status == SessionStatus.BLOCKED:
            send_message(session, "Continue with the next step")

        time.sleep(1)
```

### CLI

`doeff-agents` is the operator-facing CLI. Run `doeff-agents <command> --help`
for flags — they are not reproduced here.

| Command | What it does | Backends | Starts a host if none is reachable? |
|---|---|---|---|
| `run` | Launch an agent session **directly in tmux**, bypassing the host | tmux only | no — never contacts the host |
| `stop` | Kill the session's terminal | tmux only | no — talks to tmux directly |
| `agentd kinds` | Print a running host's binding-kind vocabulary | all | **no — read-only by design** |
| `ps` | List sessions | all | yes |
| `watch` | Poll a session and print status changes | all | yes |
| `send` | Send a message to a running session | all | yes |
| `output` | Capture recent output from a session | all | yes |
| `attach` | Attach the terminal to the session | tmux only | yes |
| `agentd ensure` | Ensure a host is reachable | all | yes — that is the command's purpose |
| `agentd adopt` | Register an already-running session as an observation | all | yes |
| `agentd turn-open` / `turn-close` | Stamp a turn open / closed | all | yes |
| `agentd by-conversation` | Resolve a conversation id to its ledger row | all | yes |

The last column matters on a machine where a supervisor (launchd, systemd) owns
the host: every command marked *yes* goes through `ensure_agentd`, which starts a
host when the expected socket has no listener. `agentd kinds` is the only
command that observes without that side effect — kind verification must never
couple to host liveness, so an unreachable host means "no observation" (exit 1
with the start command in the message) rather than "start a host".

```bash
# Read-only: what kinds does the already-running host advertise?
doeff-agents agentd kinds

# Ensure a host is reachable — this one starts it if needed — and print status.
doeff-agents agentd ensure --json
```

All of these resolve the socket from the environment: `DOEFF_AGENTD_SOCKET` when it is
set, else `$XDG_RUNTIME_DIR/doeff/agentd.sock`, else
`/tmp/doeff-agentd-$USER.sock`. A host started by `join` listens under its state
directory (below), which the defaults never name, so point the CLI at it:

```bash
export DOEFF_AGENTD_SOCKET="${XDG_STATE_HOME:-$HOME/.local/state}/doeff/acp-agentd/agentd.sock"
doeff-agents agentd kinds
doeff-agents ps
```

There is no flag for this: the socket is a property of the machine's host, not
of a single command, and the variable is the same one that tells an agent
process where its host is. Only the socket is named — the database and log stay
at their XDG paths, so `agentd ensure` still refuses (loudly) to adopt a host
whose database differs from the canonical one.

## Session host (`doeff-sessionhost`) and the agentd socket

The session host ships with this package as the console script
**`doeff-sessionhost`**. The Rust `doeff-agentd` binary it replaced is retired
and is no longer a spawn target (ADR-DOE-AGENTS-004, law
`retired-impl-lives-only-in-git-history`); only the socket, database, and
protocol keep the `agentd` name.

The host and the Python client share one canonical convention:

- DB: `${XDG_STATE_HOME:-$HOME/.local/state}/doeff/agentd.sqlite`
- socket when `XDG_RUNTIME_DIR` is set: `$XDG_RUNTIME_DIR/doeff/agentd.sock`
- socket when `XDG_RUNTIME_DIR` is unset: `/tmp/doeff-agentd-${USER:-unknown}.sock`

Start the host with the same derived paths:

```bash
AGENTD_DB="${XDG_STATE_HOME:-$HOME/.local/state}/doeff/agentd.sqlite"
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
  AGENTD_SOCKET="$XDG_RUNTIME_DIR/doeff/agentd.sock"
else
  AGENTD_SOCKET="/tmp/doeff-agentd-${USER:-unknown}.sock"
fi
doeff-sessionhost --db "$AGENTD_DB" --socket "$AGENTD_SOCKET" --max-running 10 serve
```

`serve` is the only command, and it may be omitted; unknown arguments are
rejected. Run `doeff-sessionhost --help` for the flags, the environment
variables that shadow them, and the two env-only knobs. That listing is
generated from the flag vocabulary of record — `parse-args` in
`src/doeff_agents/sessionhost/host.hy` — so it cannot drift from what the host
accepts. Two flags are easy to get wrong: `--max-running none` (or `unlimited`)
means no limit, and `--max-running 0` is refused at startup because it would
reject every launch. `--acp` (or `DOEFF_AGENTD_ACP=on`, off by default)
additionally runs the agentd thread that joins a control-plane cluster.

`LazyAgentdClient` connects only to that expected socket. It does not probe
per-run temporary sockets or fall back to direct worker execution; if no host is
reachable, it raises an actionable error containing the exact start command.

### Joining a cluster: `doeff-sessionhost join`

Adding a machine to an agent control plane is one command, and it is the same
command on macOS (launchd), Linux (systemd), a GCP node, and a runner pod:

```bash
doeff-sessionhost join --server <URL> --token-file <file> [--config <agentd.toml>]
```

The plan is derived at one point from the declaration — flags override the
`--config` file, which overrides the defaults — and then the host runs as above.

- Declaration schema: `doeff.agentd-join.v1`. A file declaring anything else is
  refused.
- Default state directory: `$XDG_STATE_HOME/doeff/acp-agentd`, holding
  `agentd.sqlite`, `agentd.sock`, `headless-events/`, and `record-spool/`.
- Further flags name the node, its ownership and custody, capacity, place, work
  roots, backend, session hooks, borrowed credentials, and the record sink: run
  `doeff-sessionhost join --help`, which is generated from the flag table at the
  top of `src/doeff_agents/sessionhost/acp/join.hy`. `--capacity`, `--place` and
  `--record` are required alongside `--server` and `--token-file`; a node that
  does not declare them refuses to join rather than guessing.

## What each backend supports

The backend vocabulary is `{tmux, herdr, headless}`. The default is `tmux`;
`DOEFF_SESSIONHOST_BACKEND` selects another one and `--backend` overrides that.

| Operation | tmux | herdr | headless |
|---|---|---|---|
| Start — host RPC `session.launch` | yes | yes | yes |
| Resume — host RPC `session.resume` | yes | yes | yes |
| Start — `doeff-agents run` (direct, not via the host) | yes | **no** | **no** |
| Attach — `doeff-agents attach` | yes | **no** | **no** |
| Stop — `doeff-agents stop` | yes | **no** | **no** |

Current limitations of the CLI, stated as they are rather than as they should be:

- `attach` refuses a non-tmux session explicitly, naming the backend it found.
- `stop` only knows tmux. Against a herdr or headless session it reports
  `Session not found` — it does not say that the backend is the reason.
- There is therefore **no CLI stop for a headless session**. End it through the
  control plane (`session.cancel`, which is terminal) or by sending `SIGTERM` to
  the host, which closes running turns before the socket stops accepting.

## Agent Launch Invariant

Print mode has exactly one home: the headless backend.

- On the terminal backends (`tmux`, `herdr`), agents are launched as live
  terminal sessions. The initial task prompt and every follow-up prompt are
  delivered through the terminal transport after launch (`tmux send-keys`
  today; another terminal backend such as zellij can provide the same operation
  later). Starting an agent there in print, one-shot, SDK, or prompt-argv mode
  is an architecture violation: built-in adapters must not read
  `LaunchParams.prompt` while building argv, and Claude/Codex prompt-mode flags
  or positional prompt arguments are banned. This keeps the agent process alive
  so doeff-agents can validate structured results and send correction prompts in
  the same session.
- The headless backend is the one sanctioned print-mode launch site. The
  spelling of Claude's print mode appears in `sessionhost/impls/headless_argv.hy`
  and nowhere else under `sessionhost/`, and every RPC arm selects it through the
  single `headless-backend?` predicate. The semgrep rule
  `doeff-agents-no-claude-print-mode` keeps the ban in force everywhere else.

The statement of record is ADR-DOE-AGENTS-012 R11, law
`print-mode-has-one-home-the-headless-backend`
(`docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy`). Related contracts —
what the host owns and does not own, how exclusivity is decided, and why the
public launch surface is auth-blind — are stated in ADR-DOE-AGENTS-004; launch
readiness and prompt delivery in ADR-DOE-AGENTS-011; resume and fork in
ADR-DOE-AGENTS-006. This README points at them rather than restating them.

## Features

- **Terminal abstraction**: Pythonic wrapper around the terminal backends
- **Adapter pattern**: Protocol-based adapters for different agents
- **Status monitoring**: Detect agent state from session output
- **Session lifecycle**: Create, monitor, send, capture, kill
- **Async support**: Both sync and async APIs
- **Context managers**: Safe cleanup on exceptions/cancellation

## Supported Agents

- Claude Code (`claude`)
- OpenAI Codex (`codex`)
- Gemini CLI (`gemini`)

## Verified revisions

This document was checked against doeff `564b03aa`. A consuming project may
still pin an older doeff together with the engine and client revisions it was
verified against; moving such a pin is that project's decision, not a
consequence of this document.

## License

MIT
