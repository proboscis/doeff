# MCP Tools — Exposing defk to Agents

doeff-agents can expose `defk` functions as [MCP](https://modelcontextprotocol.io/) tools
to Claude Code agents. The agent discovers tools via `.mcp.json`, connects over SSE,
and calls them through the doeff handler stack.

## Table of Contents

- [Architecture](#architecture)
- [Defining Tools](#defining-tools)
  - [From Hy (defmcp-tool)](#from-hy-defmcp-tool)
  - [From Python](#from-python)
- [Launching Agents with Tools](#launching-agents-with-tools)
- [Handler Stack Ordering](#handler-stack-ordering)
- [How It Works](#how-it-works)
- [Examples](#examples)
  - [Minimal Example](#minimal-example)
  - [Trading Agent with nak Tools](#trading-agent-with-nak-tools)
- [API Reference](#api-reference)

## Architecture

```
program (domain handlers installed)
  │
  ├── Launch(:mcp-tools [tool1, tool2, ...])
  │     │
  │     └── claude_handler (defhandler):
  │          1. Trust work_dir in ~/.claude.json
  │          2. GetHandlers(k) → captures domain handler stack
  │          3. Starts SSE MCP server on localhost:PORT
  │          4. Writes .mcp.json to work_dir
  │          5. Launches Claude Code in tmux
  │
  │     On tool call from agent:
  │          POST /message → JSON-RPC tools/call
  │          → tool.handler(*args) → Expand[T]
  │          → WithHandler(h, program) for each captured handler
  │          → doeff.run(program) → result
  │          → SSE response to agent
  │
  └── Monitor / Stop (Stop shuts down MCP server)
```

Each `Launch` gets an isolated MCP server: separate port, separate `.mcp.json`,
separate captured handler stack, tracked per-session in the handler's `lazy-var mcp-servers`.

## Defining Tools

### From Hy (defmcp-tool)

```hy
(require doeff-hy.macros [defmcp-tool <-])

(defmcp-tool submit-order
  "Submit a trading order to Kabustation"
  [{:name "symbol" :type "string" :description "Stock symbol code (e.g. 7203)"}
   {:name "side" :type "string" :enum ["buy" "sell"] :description "Order side"}
   {:name "qty" :type "integer" :description "Quantity to trade"}]
  (<- result (submit-order symbol side qty))
  result)
```

This expands to:
1. A `@do` handler function `_submit_order_mcp_handler(symbol, side, qty)`
2. An `McpToolDef` instance `submit_order` with name, description, params, and handler

**Param fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `:name` | yes | Parameter name (becomes function argument) |
| `:type` | yes | JSON Schema type: `"string"`, `"integer"`, `"number"`, `"boolean"` |
| `:description` | yes | Description shown to the agent |
| `:enum` | no | Allowed values: `["buy" "sell"]` |
| `:required` | no | Default `True`. Set `False` for optional params |
| `:default` | no | Default value when param is omitted |

**Body:** Same as `defk` — supports `<-` binding and `!` (bang) expansion.
In practice the body is usually a single line calling an existing `defk`.

### From Python

```python
from doeff import do
from doeff.mcp import McpToolDef, McpParamSchema

@do
def echo_handler(message):
    return f"Echo: {message}"

echo_tool = McpToolDef(
    name="echo",
    description="Echo the message back",
    params=(
        McpParamSchema(name="message", type="string", description="Message to echo"),
    ),
    handler=echo_handler,
)

# Inspect the generated JSON Schema
echo_tool.input_schema()
# → {"type": "object", "properties": {"message": {"type": "string", ...}}, "required": ["message"]}
```

## Launching Agents with Tools

`LaunchEffect` has flat fields — pass `mcp_tools` directly:

```python
from pathlib import Path
from doeff_agents.effects import Launch
from doeff_agents.adapters.base import AgentType

launch_eff = Launch(
    "my-session",
    agent_type=AgentType.CLAUDE,
    work_dir=Path("/tmp/workspace"),
    prompt="Use the my-tool tool to process data.",
    mcp_tools=(my_tool,),
)
```

From Hy:

```hy
(import doeff_agents.effects [Launch])
(import doeff_agents.adapters.base [AgentType])
(import pathlib [Path])

(<- handle (Launch "my-session"
             :agent-type AgentType.CLAUDE
             :work-dir (Path "/tmp/workspace")
             :prompt "Use the my-tool tool."
             :mcp-tools #(my-tool)))
```

## Handler Stack Ordering

**Critical:** The Claude handler must be **outermost** relative to the domain handlers.
This allows `GetHandlers(k)` inside the handler to capture domain handlers from the
continuation chain.

```
WithHandler(claude_handler,      ← outermost: catches Launch, captures inner handlers
  WithHandler(domain_handler_1,  ← captured via GetHandlers(k)
    WithHandler(domain_handler_2,← captured via GetHandlers(k)
      program)))                 ← performs Launch effect
```

If the Claude handler is innermost, `GetHandlers(k)` will not see domain handlers,
and tool calls will fail with "no handler found for effect."

The new canonical entry point:

```python
from doeff import WithHandler, run
from doeff_agents.handlers import claude_agent_handler

agent = claude_agent_handler()
wrapped = WithHandler(agent, WithHandler(nak_handler, program))
result = run(wrapped)
```

## How It Works

1. **`defmcp-tool`** creates an `McpToolDef` with a `@do` handler function and MCP metadata (description, JSON Schema params).

2. **`LaunchEffect.mcp_tools`** carries the tool tuple as a flat field on the effect.

3. **`claude_handler` (Hy defhandler)** catches `LaunchEffect` when `agent_type=CLAUDE`:
   - Calls `_trust-workdir` to mark the workspace as trusted in `~/.claude.json`
   - When `mcp_tools` is non-empty: yields `GetHandlers(k)` to capture the handler stack
   - Creates a `run_tool` closure that wraps tool programs with `WithHandler`
   - Starts an `McpToolServer` and writes `.mcp.json` to `work_dir`
   - Creates the tmux session and launches `claude --dangerously-skip-permissions ...`
   - Stores session + server in `lazy-var sessions` / `lazy-var mcp-servers`
   - Resumes with `SessionHandle`

4. **Claude Code** launches in tmux, reads `.mcp.json`, connects to the SSE server.

5. **Tool call flow:**
   - Agent sends `tools/call` JSON-RPC via POST
   - Server calls `run_tool(tool, arguments)`
   - `run_tool` builds `tool.handler(*args)` → wraps with captured handlers → `doeff.run()`
   - Result returned as JSON-RPC response via SSE

6. **Cleanup:** `StopEffect` shuts down the MCP server for the session
   (`mcp-servers.pop(session_name).shutdown()`).

## Examples

### Minimal Example

```python
from pathlib import Path
from doeff import do, run, Perform, WithHandler
from doeff.mcp import McpToolDef, McpParamSchema
from doeff_agents.adapters.base import AgentType
from doeff_agents.effects import LaunchEffect, Monitor, Sleep, Capture, Stop
from doeff_agents.handlers import claude_agent_handler

# 1. Define a tool
@do
def greet(name):
    return f"Hello, {name}!"

greet_tool = McpToolDef(
    name="greet",
    description="Greet someone by name",
    params=(McpParamSchema(name="name", type="string", description="Person's name"),),
    handler=greet,
)

# 2. Build the program
@do
def main():
    handle = yield Perform(LaunchEffect(
        session_name="demo",
        agent_type=AgentType.CLAUDE,
        work_dir=Path("/tmp/mcp-demo"),
        prompt='Use the greet tool to greet "World".',
        mcp_tools=(greet_tool,),
    ))

    # Monitor until done
    for _ in range(60):
        yield Sleep(1.0)
        obs = yield Monitor(handle)
        if obs.is_terminal:
            break

    output = yield Capture(handle, lines=50)
    yield Stop(handle)
    return output

# 3. Run with claude handler (it catches Launch and manages MCP)
result = run(WithHandler(claude_agent_handler(), main()))
print(result)
```

### Trading Agent with nak Tools

```hy
(require doeff-hy.macros [defmcp-tool defp <- defhandler])
(import doeff [WithHandler run])
(import doeff_agents.effects [Launch Monitor Sleep Capture Stop])
(import doeff_agents.handlers [claude-agent-handler])
(import doeff_agents.adapters.base [AgentType])
(import pathlib [Path])

;; Tools wrapping existing defk functions
(defmcp-tool query-position-tool
  "Query current position for a stock symbol"
  [{:name "symbol" :type "string" :description "Stock symbol (e.g. 7203)"}]
  (<- result (query-position symbol))
  result)

(defmcp-tool submit-order-tool
  "Submit a trading order"
  [{:name "symbol" :type "string" :description "Stock symbol"}
   {:name "side" :type "string" :enum ["buy" "sell"] :description "Order side"}
   {:name "qty" :type "integer" :description "Quantity"}]
  (<- result (submit-order symbol side qty))
  result)

(defmcp-tool query-price-tool
  "Get current price for a symbol"
  [{:name "symbol" :type "string" :description "Stock symbol"}]
  (<- result (query-price symbol))
  result)

;; Main program
(defp trading-agent
  {:post [(: % str)]}
  (<- handle (Launch "trader"
               :agent-type AgentType.CLAUDE
               :work-dir (Path "/tmp/trading-agent")
               :prompt "Check 7203 price, buy 100 shares if under 2500."
               :mcp-tools #(query-position-tool submit-order-tool query-price-tool)))
  ;; Monitor...
  (<- output (Capture handle :lines 200))
  (<- (Stop handle))
  output)

;; Run: claude-agent-handler outermost, nak handlers inner
(run
  (WithHandler (claude-agent-handler)
    (WithHandler nak-handler
      trading-agent)))
```

## API Reference

### `McpParamSchema`

```python
@dataclass(frozen=True)
class McpParamSchema:
    name: str         # Parameter name
    type: str         # JSON Schema type
    description: str  # Description for the agent
    required: bool = True
    enum: tuple[str, ...] | None = None
    default: Any = MISSING
```

### `McpToolDef`

```python
@dataclass(frozen=True)
class McpToolDef:
    name: str                          # Tool name (shown to agent)
    description: str                   # Tool description (shown to agent)
    params: tuple[McpParamSchema, ...]  # Input parameters
    handler: Callable[..., Expand]     # @do function returning DoExpr

    def input_schema(self) -> dict     # Generate MCP JSON Schema
    def param_names(self) -> tuple[str, ...]
```

### `LaunchEffect` (flat fields)

```python
@dataclass(frozen=True, kw_only=True)
class LaunchEffect:
    session_name: str
    agent_type: AgentType
    work_dir: Path
    prompt: str | None = None
    model: str | None = None
    mcp_tools: tuple[McpToolDef, ...] = ()
    ready_timeout: float = 30.0
```

### `Launch(...)` constructor

```python
def Launch(
    session_name: str,
    *,
    agent_type: AgentType,
    work_dir: Path,
    prompt: str | None = None,
    model: str | None = None,
    mcp_tools: tuple[McpToolDef, ...] = (),
    ready_timeout: float = 30.0,
) -> LaunchEffect: ...
```

### `claude_agent_handler(*, backend=None)`

Returns the Hy-based `defhandler` that catches `LaunchEffect(agent_type=CLAUDE)`,
manages trust, MCP, tmux, and session lifecycle.

```python
from doeff_agents.handlers import claude_agent_handler
handler = claude_agent_handler()  # optional: backend=my_backend
```

### `McpToolServer`

```python
class McpToolServer:
    def __init__(self, tools, run_tool, *, port=0): ...
    def start(self) -> None      # Start in daemon thread
    def shutdown(self) -> None   # Stop server and close SSE sessions
    @property
    def url(self) -> str         # SSE endpoint URL (e.g. http://127.0.0.1:52119/sse)
    @property
    def port(self) -> int
```

## Migration Notes

The old OOP handler API (`_make_protocol_handler`, `TmuxAgentHandler`, `LaunchConfig`
wrapper inside `LaunchEffect`) is **deprecated**. New code should:

- Use `LaunchEffect` / `Launch(...)` with flat fields (no `config=` wrapper)
- Use `claude_agent_handler()` instead of `_make_protocol_handler(TmuxAgentHandler())`
- Place the handler **outermost** relative to domain handlers

The legacy `LaunchConfig` dataclass (in `adapters.base`) is retained only for the
imperative `session.py` / CLI API. The effects API uses flat fields on `LaunchEffect`.
