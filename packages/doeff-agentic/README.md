# doeff-agentic

Agent-based workflow orchestration combining doeff-flow (durable execution) and doeff-agents (session management).

## Overview

doeff-agentic provides a higher-level abstraction for orchestrating multi-agent workflows:

- **Effect-based agent invocation** - Use `RunAgent`, `SendMessage`, etc. in doeff workflows
- **State file management** - Track workflow and agent state for CLI/plugin consumers
- **Real-time observability** - Monitor workflows via CLI or API
- **Human-in-the-loop** - Pause workflows for human approval

## Installation

```bash
pip install doeff-agentic
# or
uv add doeff-agentic
```

## Quick Start

### Define a Workflow

```python
from doeff import do, slog
from doeff_agentic import RunAgent, AgentConfig

@do
def pr_review_workflow(pr_url: str):
    yield slog(status="reviewing", msg="Starting review")

    review = yield RunAgent(
        config=AgentConfig(
            agent_type="claude",
            prompt=f"Review the PR at {pr_url}",
            profile="code-review",
        ),
        session_name="review-agent",
    )

    if "issues" in review.lower():
        yield slog(status="fixing", msg="Fixing issues")
        yield RunAgent(
            config=AgentConfig(
                agent_type="claude",
                prompt=f"Fix these issues: {review}",
            ),
            session_name="fix-agent",
        )

    return review
```

### Run the Workflow

```python
from doeff import WithHandler, default_handlers, run
from doeff_agentic import agentic_effectful_handlers

program = WithHandler(
    handler=agentic_effectful_handlers(workflow_name="pr-review"),
    expr=pr_review_workflow("https://github.com/..."),
)
result = run(program, handlers=default_handlers())
```

### Monitor via CLI

```bash
# List workflows
doeff-agentic ps

# Watch a workflow
doeff-agentic watch a3f

# Attach to agent
doeff-agentic attach a3f

# Send message
doeff-agentic send a3f "continue"

# View logs
doeff-agentic logs a3f

# Stop workflow
doeff-agentic stop a3f
```

## Effects

### RunAgent

Launch an agent and wait for completion:

```python
result = yield RunAgent(
    config=AgentConfig(
        agent_type="claude",  # claude, codex, gemini
        prompt="Your prompt here",
        profile="optional-profile",
    ),
    session_name="my-agent",
)
```

### SendMessage

Send a message to a running agent:

```python
yield SendMessage("my-agent", "Continue with step 2")
```

### WaitForStatus

Wait for an agent to reach a specific status:

```python
status = yield WaitForStatus("my-agent", AgentStatus.BLOCKED, timeout=60)
```

### CaptureOutput

Capture current agent output:

```python
output = yield CaptureOutput("my-agent", lines=100)
```

### WaitForUserInput

Pause for human input:

```python
response = yield WaitForUserInput(
    "my-agent",
    prompt="Approve changes? [yes/no]",
    timeout=300,
)
```

## Python API

```python
from doeff_agentic.api import AgenticAPI

api = AgenticAPI()

# List workflows
workflows = api.list_workflows(status=["running", "blocked"])

# Get workflow by ID or prefix
wf = api.get_workflow("a3f")

# Watch for updates
for update in api.watch("a3f"):
    print(update.workflow.status, update.event)

# Agent operations
api.attach("a3f")
api.send_message("a3f", "continue")
api.stop("a3f")

# Get agent output
output = api.get_agent_output("a3f", agent="review-agent")
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `ps` | List running workflows and agents |
| `show <id>` | Show workflow details |
| `watch <id>` | Monitor workflow in real-time |
| `attach <id>` | Attach to agent's tmux session |
| `logs <id>` | View agent output history |
| `send <id> <msg>` | Send message to agent |
| `stop <id>` | Stop workflow and kill agents |

All commands support `--json` for plugin consumption.

## Fast Rust CLI

For plugin integration, a fast Rust CLI is available at `packages/doeff-agentic-cli`:

```bash
# Build
cd packages/doeff-agentic-cli
cargo build --release

# Use (~5ms startup vs ~300ms for Python)
./target/release/doeff-agentic ps --json
```

## State Files

Workflow state is stored at `~/.local/state/doeff-agentic/`:

```
workflows/
├── a3f8b2c/
│   ├── meta.json           # workflow metadata
│   ├── trace.jsonl         # effect trace
│   └── agents/
│       ├── review-agent.json
│       └── fix-agent.json
└── index.json              # id → name mapping
```

## Examples

See `examples/` directory for progressive tutorials:

1. **Hello Agent** - Minimal single-agent workflow
2. **Agent with Status** - Using slog for observability
3. **Sequential Agents** - Chaining agent outputs
4. **Conditional Flow** - Branching based on results
5. **Human-in-the-Loop** - Pause for approval
6. **Parallel Agents** - Multiple perspectives
7. **PR Review Workflow** - Complete production example

## Related Packages

- **doeff-flow** - Durable execution and observability
- **doeff-agents** - Agent session management

## License

MIT
