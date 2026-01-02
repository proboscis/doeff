# doeff-agentic Examples

Progressive examples that teach you how to build agent workflows.

## Prerequisites

1. Install doeff-agentic:
   ```bash
   cd packages/doeff-agentic
   uv sync
   ```

2. Ensure you have an agent CLI available (e.g., Claude Code):
   ```bash
   claude --version
   ```

3. Ensure tmux is installed:
   ```bash
   tmux -V
   ```

## Examples

### 01. Hello Agent
Minimal example - launch a single agent and get output.
```bash
uv run python examples/01_hello_agent.py
```

### 02. Agent with Status
Show workflow progress using slog (structured logging).
```bash
uv run python examples/02_agent_with_status.py
# In another terminal:
doeff-agentic watch <workflow-id>
```

### 03. Sequential Agents
Chain multiple agents - output of one feeds into the next.
```bash
uv run python examples/03_sequential_agents.py
```

### 04. Conditional Flow
Branch based on agent output.
```bash
uv run python examples/04_conditional_flow.py
```

### 05. Human-in-the-Loop
Pause workflow for human review.
```bash
uv run python examples/05_human_in_loop.py
# When waiting, in another terminal:
doeff-agentic send <workflow-id> "approve"
```

### 06. Parallel Agents
Run multiple agents with different perspectives.
```bash
uv run python examples/06_parallel_agents.py
```

### 07. PR Review Workflow
Complete production-style workflow combining all patterns.
```bash
uv run python examples/07_pr_review_workflow.py https://github.com/org/repo/pull/123
```

## Monitoring Workflows

While examples are running, you can monitor them:

```bash
# List all workflows
doeff-agentic ps

# Watch a specific workflow
doeff-agentic watch <workflow-id>

# Attach to agent's tmux session
doeff-agentic attach <workflow-id>

# View agent output
doeff-agentic logs <workflow-id>

# Send a message to an agent
doeff-agentic send <workflow-id> "your message"

# Stop a workflow
doeff-agentic stop <workflow-id>
```

## Using the API

You can also use the Python API directly:

```python
from doeff_agentic.api import AgenticAPI

api = AgenticAPI()

# List workflows
workflows = api.list_workflows()

# Get workflow details
wf = api.get_workflow("a3f")

# Watch for updates
for update in api.watch("a3f"):
    print(update.workflow.status)

# Send message
api.send_message("a3f", "continue")
```
