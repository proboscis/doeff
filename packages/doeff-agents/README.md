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
host: `doeff-agents agentd kinds` reports the binding-kind vocabulary of one and
`doeff-agents ps` lists what it is carrying. Neither starts a host — no
observation does (see the table below). With no host reachable they exit 1 and
name the start command — after a doeff effect traceback, which is noise, not a
second failure.

Launching a real agent additionally needs an authenticated CLI on the machine.
The credential state is Claude Code's or Codex's own, and it reaches the host as
a *binding* built by the control plane; the host advertises the binding kinds it
accepts (`agentd kinds`) and a declaration naming an unknown kind or version is
refused. This package never accepts LLM provider API keys **via the env
boundary** — not through `session_env`, `ClaudeRuntimePolicy.bootstrap_exports`,
wrappers or shell exports — see `AGENTS.md` (Agent Authentication Boundary) in
the repository root. That refusal is unconditional; the metered kinds below do
not relax it, they are a different route.

Pay-per-use (metered) billing is **declared by the binding kind and permitted by
host policy**. The kinds `claude-code-metered` (`{config_dir}`) and
`codex-metered` (`{auth_file, profile_dir}`) say "this home bills per use", and
only a host started with `--allow-metered-billing` (off by default; no
environment variable turns it on) admits them. The credential stays in the CLI's
own home, written by the CLI's own tool — for Claude, a non-empty `apiKeyHelper`
or the `CLAUDE_CODE_USE_VERTEX=1` + `ANTHROPIC_VERTEX_PROJECT_ID` pair in
`settings.json`. (An `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` entry in
`settings.json`'s `env` block counts as a metered declaration but is *not*
accepted — put the key behind `apiKeyHelper` instead.) For Codex it is a
non-empty `OPENAI_API_KEY` in `auth.json`, written by `codex login
--with-api-key` (a `null` `OPENAI_API_KEY`, which `codex login` writes for
ChatGPT accounts, does not count). Before launch the host only checks that the
home *declares* one and records the home path(s) with a `"billing": "metered"`
marker: the value never passes through the host (ADR-DOE-AGENTS-004 R9, law
`metered-billing-is-declared-by-kind-and-allowed-by-host-policy`). Conversely,
the subscription kinds (`claude-code` / `codex`) refuse a home that declares a
metered credential: the billing class must be visible in the binding kind.

Changing the env-boundary refusal is a design question, tracked by the consuming
project's handover notes — not something to work around here.

## Using this from an agent control plane

Three pieces stack up: the control-plane **engine** schedules work, the
**client** (`doeff-agent-haskell`) talks to it, and **this package** is the host
that actually runs the agent on a machine.

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
| `agentd kinds` | Print a running host's binding-kind vocabulary | all | **no — observation** |
| `ps` | List sessions | all | **no — observation** (`--ensure` opts in) |
| `watch` | Poll a session and print status changes | all | **no — observation** (`--ensure` opts in) |
| `output` | Capture recent output from a session | all | **no — observation** (`--ensure` opts in) |
| `agentd by-conversation` | Resolve a conversation id to its ledger row | all | **no — observation** (`--ensure` opts in) |
| `stop` | End the session through the host (`session.cancel`) | all | yes |
| `send` | Send a message to a running session | all | yes |
| `attach` | Attach the terminal to the session | tmux only | yes |
| `agentd ensure` | Ensure a host is reachable | all | yes — that is the command's purpose |
| `agentd adopt` | Register an already-running session as an observation | all | yes |
| `agentd turn-open` / `turn-close` | Stamp a turn open / closed | all | yes |

The last column matters on a machine where a supervisor (launchd, systemd) owns
the host. **Reading never starts a host**: an unreachable host means "no
observation" — exit 1 with the start command in the message — because starting a
competitor during the supervisor's own restart window binds a rogue unsupervised
host against the same socket and store (ADR-DOE-AGENTS-004 laws
`reads-never-start-a-host` and `liveness-authority-is-the-socket`). That default
is structural rather than a prompt, since these commands run unattended. Exactly
two things start a host: the `agentd ensure` verb, and an explicit `--ensure` on
an observation.

Commands that change a session (`stop`, `send`, `attach`) do go through
`ensure_agentd`, because the host is the authority that owns the change.

```bash
# Observation: what kinds does the already-running host advertise, and what is
# it carrying?  Neither starts a host.
doeff-agents agentd kinds
doeff-agents ps

# Ensure a host is reachable — this one starts it if needed — and print status.
doeff-agents agentd ensure --json

# End a session, whatever backend carries it.
doeff-agents stop <session>
```

All of these resolve the socket from the environment: `DOEFF_AGENTD_SOCKET` when it is
set, else `$XDG_RUNTIME_DIR/doeff/agentd.sock`, else
`/tmp/doeff-agentd-$USER.sock`. A host started with `--socket` elsewhere is not
found by the defaults, so point the CLI at it:

```bash
export DOEFF_AGENTD_SOCKET=/path/to/agentd.sock
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
variables that shadow them, and the env-only knob. That listing is
generated from the flag vocabulary of record — `parse-args` in
`src/doeff_agents/sessionhost/host.hy` — so it cannot drift from what the host
accepts. Two flags are easy to get wrong: `--max-running none` (or `unlimited`)
means no limit, and `--max-running 0` is refused at startup because it would
reject every launch.

`LazyAgentdClient` connects only to that expected socket. It does not probe
per-run temporary sockets or fall back to direct worker execution; if no host is
reachable, it raises an actionable error containing the exact start command.

## What each backend supports

The backend vocabulary is `{tmux, herdr}`. The default is `tmux`;
`DOEFF_SESSIONHOST_BACKEND` selects another one and `--backend` overrides that.

| Operation | tmux | herdr |
|---|---|---|
| Start — host RPC `session.launch` | yes | yes |
| Resume — host RPC `session.resume` | yes | yes |
| Stop — `doeff-agents stop` | yes | yes |
| Start — `doeff-agents run` (direct, not via the host) | yes | **no** |
| Attach — `doeff-agents attach` | yes | **no** |

`stop` is backend-blind because the host's `session.cancel` is: whichever
substrate is installed answers the same kill effect, so the CLI does not branch
on the backend. The row ends as `stopped` with cause `cancelled`, and a session
already terminal stays as it was.

The table states what each backend implements. What was measured end to end is
narrower and worth stating separately. On 2026-09-15, against herdr 0.8.2
(protocol 20) and an isolated host, a **launched** herdr session — one the host
created, so doeff owns its workspace label — stopped in 0.25s: exit 0, row
`stopped` with cause `cancelled` and `finished_at` stamped, the label's holder
set empty, `pane.list` answering `workspace_not_found`, and the pane's child
process gone. The tmux launched sessions were measured the same way on the same
day and the same base — exit 0, substrate gone, row `stopped` with cause
`cancelled`. A herdr seat that only lives in herdr's **agent registry**, with
no doeff-owned workspace label (an interactive seat some other tool named), is a
deliberately different case: the host observes it and `agentd adopt` accepts it
with `substrate_present: true`, but `stop` refuses it — exit 1, `herdr
kill-session failed`, the row left `running` and the seat still alive. doeff does
not close a seat it did not create (ADR-DOE-AGENTS-004 R12, law
`herdr-session-identity-is-workspace-label`).

The one CLI limitation left, stated as it is rather than as it should be:

- `attach` is tmux-only. It refuses a non-tmux session explicitly, naming the
  backend it found. A herdr session is reachable through herdr's own attach.

## Agent Launch Invariant

The session host never launches an agent in print mode.

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
- The print-mode argv lives in the `doeff-claude-code` package, which owns the
  process lifetime. The headless handler below is an adapter onto its effects
  and holds no argv, no child process and no pid. The session host's own
  headless backend was retired with agentd (agora-redesign #668). The semgrep
  rule `doeff-agents-no-claude-print-mode` keeps the ban in force everywhere in
  this package.

## Headless handler without the session host

`doeff_agents.handlers.headless_claude_agent_handlers(config_dir=..., env=...)`
returns the handler pair that answers the public effects for Claude by
translating them into `doeff-claude-code`'s effects — no session-host socket,
no sqlite: `doeff-claude-code`'s production handler and the headless adapter
(`fake_headless_claude_agent_handlers(responder=...)` puts the same adapter over
`doeff-claude-code`'s fake). Callers need not import `doeff-claude-code`; they
install a doeff-time handler and the scheduler outside the pair.

Callers that must not name a substrate (agora keeps the substrate the
library's concern) use `claude_agent_runtime_handlers(config_dir=..., env=...)`
and `fake_claude_agent_runtime_handlers(responder=...)`: the same pairs under
names that say "the Claude agent runtime" and leave the choice of substrate
to doeff-agents.

| Public effect | What the headless handler does |
|---|---|
| `Launch(..., resume_from=None)` | starts the first turn when `prompt` is given; `resume_from` continues an earlier context (`ResumeTargetNotFoundError` when it is not here) |
| `FollowUp(handle, text, mode=NEXT_TURN)` / `Send` | runs it as the next turn — waits behind a running turn |
| `FollowUp(handle, text, mode=INJECT)` | adds it to the running turn (`NoTurnInFlightError` when none runs) |
| `Interrupt(handle)` | stops the running turn only; the session and its context stay |
| `Events(handle, after_seq=..., wait_seconds=...)` | turn events (`AgentTextEvent`, `AgentToolUseEvent`, `AgentInputFateEvent`, `AgentTurnEndEvent`, ...) and the last turn end (`AgentTurnCompleted` / `Failed` / `Interrupted` / `Lost`, each carrying `resume_from`) |
| `AwaitResult` / `Monitor` | the end of the running turn / the session status, read from the same events |
| `Stop` / `StopSession` / `ReleaseSession` | closes the session; inputs still waiting end as `discarded` |
| `Capture` / `AttachAgentSession` | refused with `AgentCapabilityUnsupportedError` (no screen) |

Terminal handlers refuse `resume_from` and `mode=INJECT` with
`AgentCapabilityUnsupportedError` instead of silently starting fresh or typing
keys. Tests: `tests/test_headless_adapter.hy` runs the same programs over the
fake, the production handler with a stand-in CLI, and (marked `e2e`) the real
CLI.

Related contracts —
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
