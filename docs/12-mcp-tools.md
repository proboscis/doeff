# MCP Tools — Exposing defk to Agents

> **Needs components outside `pip install doeff`.** This page uses `doeff-agents` and
> `doeff-time`. PyPI has only an old `doeff-agents` 0.1.1 build, which does not import with
> doeff 0.5.0, and `doeff-time` is not on PyPI, so install them from a checkout of this
> repository together with the repository packages `doeff-agents` depends on
> (`doeff-claude-code`, `doeff-codex`). pip takes everything else from PyPI:

```bash
git clone --depth 1 https://github.com/proboscis/doeff.git
pip install doeff ./doeff/packages/doeff-time ./doeff/packages/doeff-claude-code \
  ./doeff/packages/doeff-codex ./doeff/packages/doeff-agents
```

doeff-agents can expose `defk` functions as [MCP](https://modelcontextprotocol.io/) tools
to Claude Code agents. The agent discovers tools via `.mcp.json`, connects over SSE,
and calls them through the doeff handler stack.

[![A narrated video, under two and a half minutes: a defk exposed as a tool an agent calls; defmcp-tool expanding to an McpToolDef with a name, a JSON Schema and a @do handler; Launch caught by claude_agent_handler, which captures the handlers, starts the MCP server, writes .mcp.json and resumes k with a session handle; a scripted agent's calls reaching a paper broker handler, and a call outside the schema refused by the server; and the program seeing the agent's order after Stop, in either handler order](assets/videos/mcp-tools.jpg)](assets/videos/mcp-tools.mp4)

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

```text
program (domain handlers installed)
  │
  ├── Launch(:mcp-tools [tool1, tool2, ...])
  │     │
  │     └── claude_handler (defhandler):
  │          1. Trust work_dir in ~/.claude.json
  │          2. GetHandlers(k) + GetOuterHandlers() → captures the handlers
  │             below and above it
  │          3. Starts SSE MCP server on localhost:PORT, and spawns
  │             mcp_server_loop, a task in the program's own VM
  │          4. Writes .mcp.json to work_dir
  │          5. Launches Claude Code in tmux
  │
  │     On tool call from agent:
  │          POST /message → JSON-RPC tools/call
  │          → arguments checked against the tool's schema
  │          → mcp_server_loop: tool.handler(*args) → Expand[T]
  │          → reinstall each captured handler scope around the tool program
  │          → run as a task in the same VM → result
  │          → SSE response to agent
  │
  └── Monitor / Stop (Stop shuts down MCP server)
```

Each `Launch` gets an isolated MCP server: separate port, separate `.mcp.json`,
separate captured handler stack, tracked per-session in the handler's `lazy-var mcp-servers`.

## Defining Tools

### From Hy (defmcp-tool)

```hy
(require doeff-hy.macros [defk defmcp-tool <-])
(import doeff [run])

;; The existing defk the tool exposes (this one only echoes the order back)
(defk submit-order [symbol side qty]
  {:pre [(: symbol str) (: side str) (: qty int)] :post [(: % str)]}
  f"accepted: {side} {qty} of {symbol}")

;; The tool gets a name of its own, apart from the defk it calls
(defmcp-tool submit-order-tool
  "Submit a trading order to Kabustation"
  [{:name "symbol" :type "string" :description "Stock symbol code (e.g. 7203)"}
   {:name "side" :type "string" :enum ["buy" "sell"] :description "Order side"}
   {:name "qty" :type "integer" :description "Quantity to trade"}]
  (<- result (submit-order symbol side qty))
  result)

;; Run the handler the way the tool server does
(print (run (submit-order-tool.handler "7203" "buy" 100)))  ; accepted: buy 100 of 7203
```

This expands to:
1. A `@do` handler function `_submit_order_tool_mcp_handler(symbol: "str", side: "str", qty: "int")`
2. An `McpToolDef` instance `submit_order_tool` with name (`"submit-order-tool"`, the name the agent
   sees), description, params, and handler

Give the tool a name apart from the `defk` it calls. The expansion binds the tool's name to the
`McpToolDef`, so in `(defmcp-tool submit-order … (submit-order symbol side qty))` the body would call
the tool itself, not the `defk` — even when the `defk` is defined first — and the handler would fail
with `TypeError: 'McpToolDef' object is not callable`. `defmcp-tool` in this repository refuses such a
body when it expands (doeff-hy 0.5.0 from PyPI does not check it yet).

The handler's parameters carry the type written in `:type` as a string annotation, so the
type checker (doeff-hy-check, also with `--strict`) sees the body's arguments as typed. The tool
server checks every call's arguments against the same table before it calls the handler, so the
annotation also holds at run time. The table is `MCP_JSON_TYPES` in `doeff/mcp.py` — the
annotation (defmcp-tool) and the server's check (`McpToolDef.decode_arguments`) both read it:

| `:type` | Annotation | The server accepts |
|---------|------------|--------------------|
| `"string"` | `str` | a JSON string |
| `"integer"` | `int` | a JSON integer (not `true` / `false`) |
| `"number"` | `float` | a JSON number; an integer such as `2` is passed as `2.0` |
| `"boolean"` | `bool` | `true` / `false` |
| `"array"` | `list[object]` | a JSON array |
| `"object"` | `dict[str, object]` | a JSON object |

The server also refuses a value outside `:enum` and a missing required argument; the agent gets
the reason as the tool's error. A missing optional argument gets its `:default`, or `None` when
there is no default. A parameter whose `:required` is not literally `True`, or whose `:default`
is `None`, gets `| None`. Any other `:type` is an error at expansion time.

**Param fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `:name` | yes | Parameter name (becomes function argument) |
| `:type` | yes | JSON Schema type: `"string"`, `"integer"`, `"number"`, `"boolean"`, `"array"`, `"object"` |
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
print(echo_tool.input_schema())  # {'type': 'object', 'properties': {'message': {'type': 'string', 'description': 'Message to echo'}}, 'required': ['message']}
```

## Launching Agents with Tools

`LaunchEffect` has flat fields — pass `mcp_tools` directly:

```python excerpt
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

```hy excerpt
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

This page installs the Claude handler **outermost** relative to the domain handlers.
When it catches `Launch`, the handler captures the handlers on both sides of it:
`GetHandlers(k)` gives the handlers between it and the `Launch` site (the domain
handlers in this layout), and `GetOuterHandlers()` gives the handlers above it.

```text
claude_handler(                 ← outermost: catches Launch, captures inner handlers
  domain_handler_1(             ← captured via GetHandlers(k)
    domain_handler_2(           ← captured via GetHandlers(k)
      program)))                ← performs Launch effect
```

Tool programs run under both, so a domain handler answers a tool's effects in either
order: with the Claude handler inside `nak_handler`, `GetOuterHandlers()` captures
`nak_handler` instead. The video at the top of this page runs both orders
(`tools/docs-video/src/pages/mcp-tools/measure/agent_flow.py`, with and without `inner`).

The new canonical entry point:

```python excerpt
from doeff import run
from doeff_agents.handlers import claude_agent_handler

agent = claude_agent_handler()
wrapped = agent(nak_handler(program))
result = run(wrapped)
```

## How It Works

1. **`defmcp-tool`** creates an `McpToolDef` with a `@do` handler function and MCP metadata (description, JSON Schema params).

2. **`LaunchEffect.mcp_tools`** carries the tool tuple as a flat field on the effect.

3. **`claude_handler` (Hy defhandler)** catches `LaunchEffect` when `agent_type=CLAUDE`:
   - Calls `_trust-workdir` to mark the workspace as trusted in `~/.claude.json`
   - When `mcp_tools` is non-empty: yields `GetHandlers(k)` and `GetOuterHandlers()` to capture
     the handlers below and above it
   - Starts an `McpToolServer` and writes `.mcp.json` to `work_dir`
   - Spawns `mcp_server_loop`, a task in the program's own VM that reinstalls the captured
     handler scopes around each tool program
   - Creates the tmux session and launches `claude --dangerously-skip-permissions ...`
   - Stores session + server in `lazy-var sessions` / `lazy-var mcp-servers`
   - Resumes with `SessionHandle`

4. **Claude Code** launches in tmux, reads `.mcp.json`, connects to the SSE server.

5. **Tool call flow:**
   - Agent sends `tools/call` JSON-RPC via POST
   - The server checks the arguments against the tool's schema (`McpToolDef.decode_arguments`)
     and refuses a call that does not fit, with the reason as the tool's error
   - `mcp_server_loop` builds `tool.handler(*args)`, wraps it with the captured handlers and runs
     it as a task in the same VM, so the scheduler and state are the program's own
   - Result returned as JSON-RPC response via SSE

6. **Cleanup:** `StopEffect` shuts down the MCP server for the session
   (`mcp-servers.pop(session_name).shutdown()`).

## Examples

### Minimal Example

```python excerpt
from pathlib import Path
from doeff import do, run, Perform
from doeff.mcp import McpToolDef, McpParamSchema
from doeff_agents.adapters.base import AgentType
from doeff_agents.effects import LaunchEffect, Monitor, Capture, Stop
from doeff_agents.handlers import claude_agent_handler
from doeff_time import Delay, sync_time_handler

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
        yield Delay(1.0)
        obs = yield Monitor(handle)
        if obs.is_terminal:
            break

    output = yield Capture(handle, lines=50)
    yield Stop(handle)
    return output

# 3. Run with claude handler (it catches Launch and manages MCP).
#    The time handler answers Delay: sync_time_handler() waits on the wall clock.
result = run(sync_time_handler()(claude_agent_handler()(main())))
print(result)
```

### Trading Agent with nak Tools

```hy excerpt
(require doeff-hy.macros [defmcp-tool defp <- defhandler])
(import doeff [run])
(import doeff_agents.effects [Launch Monitor Capture Stop])
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
  ((claude-agent-handler)
    (nak-handler trading-agent)))
```

## API Reference

### `McpParamSchema`

```python excerpt
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

```python excerpt
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

```python excerpt
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

```python excerpt
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

```python excerpt
from doeff_agents.handlers import claude_agent_handler
handler = claude_agent_handler()  # optional: backend=my_backend
```

### `McpToolServer`

```python excerpt
class McpToolServer:
    def __init__(self, tools, *, port=0): ...
    def start(self, *, ready_promise=None) -> None  # Start in daemon thread
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
- Place the handler outermost relative to domain handlers, as on this page (it captures the
  handlers on both sides, so the other order also works)

The legacy `LaunchConfig` dataclass (in `adapters.base`) is retained only for the
imperative `session.py` / CLI API. The effects API uses flat fields on `LaunchEffect`.
