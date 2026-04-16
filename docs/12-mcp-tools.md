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
defp agent-session (domain handlers installed)
  │
  ├── Launch(:mcp-tools [tool1, tool2, ...])
  │     │
  │     └── protocol handler:
  │          1. GetHandlers(k) → captures domain handler stack
  │          2. Starts SSE MCP server on localhost:PORT
  │          3. Writes .mcp.json to work_dir
  │          4. Launches Claude Code in tmux
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
separate captured handler stack.

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

Pass `mcp_tools` in `LaunchConfig`:

```hy
(require doeff-hy.macros [defmcp-tool defp <-])
(import doeff_agents.effects [Launch LaunchEffect])
(import doeff_agents.adapters.base [AgentType LaunchConfig])

;; Define tools
(defmcp-tool my-tool
  "Do something useful"
  [{:name "input" :type "string" :description "Input data"}]
  (<- result (do-something input))
  result)

;; Launch agent with tools
(defp main
  {:post [(: % "SessionHandle")]}
  (<- handle (Launch "my-agent"
               (LaunchConfig
                 :agent-type AgentType.CLAUDE
                 :work-dir (Path "/tmp/workspace")
                 :prompt "Use the my-tool tool to process data."
                 :mcp-tools #(my-tool))))
  handle)
```

## Handler Stack Ordering

**Critical:** The agent protocol handler must be the **outermost** handler.
This allows `GetHandlers(k)` to capture domain handlers from the continuation chain.

```
WithHandler(agent_protocol,      ← outermost: catches Launch, captures inner handlers
  WithHandler(domain_handler_1,  ← captured via GetHandlers(k)
    WithHandler(domain_handler_2,← captured via GetHandlers(k)
      program)))                 ← performs Launch effect
```

If the protocol handler is innermost, `GetHandlers(k)` will not see domain handlers,
and tool calls will fail with "no handler found for effect."

In Hy with an interpreter:

```hy
;; Correct: agent handler outermost
(run
  (WithHandler agent-protocol-handler
    (WithHandler nak-handler
      main-program)))
```

## How It Works

1. **`defmcp-tool`** creates an `McpToolDef` with a `@do` handler function and MCP metadata (description, JSON Schema params).

2. **`LaunchConfig.mcp_tools`** carries the tool definitions to the Launch effect.

3. **Protocol handler** intercepts `LaunchEffect` when `mcp_tools` is non-empty:
   - Calls `GetHandlers(k)` to extract the handler stack from the continuation
   - Creates a `run_tool` closure that wraps tool programs with `WithHandler`
   - Delegates to `TmuxAgentHandler.handle_launch(effect, run_tool=...)`

4. **`TmuxAgentHandler`** starts the MCP server and writes `.mcp.json`:
   - `McpToolServer` binds to `127.0.0.1:0` (auto-port) in a daemon thread
   - Serves MCP JSON-RPC over SSE (`GET /sse` + `POST /message`)
   - `.mcp.json` is written to `work_dir` with the SSE URL

5. **Claude Code** launches in tmux, reads `.mcp.json`, connects to the SSE server.

6. **Tool call flow:**
   - Agent sends `tools/call` JSON-RPC via POST
   - Server calls `run_tool(tool, arguments)`
   - `run_tool` builds `tool.handler(*args)` → wraps with captured handlers → `doeff.run()`
   - Result returned as JSON-RPC response via SSE

7. **Cleanup:** `Stop` effect shuts down the MCP server for the session.

## Examples

### Minimal Example

```python
from pathlib import Path
from doeff import do, run, Perform, WithHandler
from doeff.mcp import McpToolDef, McpParamSchema
from doeff_agents.adapters.base import AgentType, LaunchConfig
from doeff_agents.effects import LaunchEffect, Monitor, Sleep, Capture, Stop
from doeff_agents.handlers import _make_protocol_handler
from doeff_agents.handlers.production import TmuxAgentHandler

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
    config = LaunchConfig(
        agent_type=AgentType.CLAUDE,
        work_dir=Path("/tmp/mcp-demo"),
        prompt='Use the greet tool to greet "World".',
        mcp_tools=(greet_tool,),
    )
    handle = yield Perform(LaunchEffect(session_name="demo", config=config))

    # Monitor until done
    for _ in range(60):
        yield Sleep(1.0)
        obs = yield Monitor(handle)
        if obs.is_terminal:
            break

    output = yield Capture(handle, lines=50)
    yield Stop(handle)
    return output

# 3. Run with protocol handler outermost
handler = TmuxAgentHandler()
protocol = _make_protocol_handler(handler)
result = run(WithHandler(protocol, main()))
print(result)
```

### Trading Agent with nak Tools

```hy
(require doeff-hy.macros [defmcp-tool defp <- defhandler])
(import doeff [WithHandler run])
(import doeff_agents.effects [Launch LaunchEffect Monitor Sleep Capture Stop])
(import doeff_agents.handlers [_make-protocol-handler])
(import doeff_agents.handlers.production [TmuxAgentHandler])
(import doeff_agents.adapters.base [AgentType LaunchConfig])
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
  (setv config (LaunchConfig
    :agent-type AgentType.CLAUDE
    :work-dir (Path "/tmp/trading-agent")
    :prompt "Check the current price of 7203, then buy 100 shares if under 2500."
    :mcp-tools #(query-position-tool submit-order-tool query-price-tool)))
  (<- handle (Launch "trader" config))
  ;; Monitor...
  (<- output (Capture handle :lines 200))
  (<- (Stop handle))
  output)

;; Run: agent-protocol outermost, nak handlers inner
(setv handler (TmuxAgentHandler))
(setv protocol (_make-protocol-handler handler))
(run
  (WithHandler protocol
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

### `LaunchConfig.mcp_tools`

```python
@dataclass(frozen=True)
class LaunchConfig:
    ...
    mcp_tools: tuple[McpToolDef, ...] = ()
```

### `McpToolServer`

```python
class McpToolServer:
    def __init__(self, tools, run_tool, *, port=0): ...
    def start(self) -> None      # Start in daemon thread
    def shutdown(self) -> None   # Stop server and close SSE sessions
    @property
    def url(self) -> str         # SSE endpoint URL
    @property
    def port(self) -> int
```
