# The launch and call scenes: a program yields Launch with two tools under claude_agent_handler (outermost) and a
# paper broker handler (inside it). No agent model runs here: the terminal is a fake one that shows Claude Code's
# ready footer, and a scripted agent replays the calls an agent makes — it reads .mcp.json, opens the SSE stream,
# lists the tools and calls them over JSON-RPC, as Claude Code does. The broker answers with fixed prices.
# Prints, in order, what happened and when (milliseconds since run() started): what the handler did at Launch,
# what the agent saw, which handler answered each tool's effect, and what the program sees after Stop
# (docs/12-mcp-tools.md, "Architecture", "Launching Agents with Tools" and "How It Works").
#
# Pass "inner" to install claude_agent_handler inside the broker instead ("Handler Stack Ordering").
import http.client
import json
import shlex
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from doeff_agents import AgentType
from doeff_agents import Launch, Stop
from doeff_agents import claude_agent_handler
from doeff_agents.tmux import SessionInfo
from doeff_core_effects.handlers import state
from doeff_core_effects.scheduler import CreateExternalPromise, Wait, scheduled

from doeff import EffectBase, Pass, Resume, do, run
from doeff import handler as install
from doeff.mcp import McpParamSchema, McpToolDef

T0 = time.monotonic()


def log(*parts):
    print(f"{(time.monotonic() - T0) * 1000:7.1f} ms  [{threading.current_thread().name}]", *parts, flush=True)


# The domain: two effects, and the defk-style programs the tools expose.
@dataclass(frozen=True)
class QuotePrice(EffectBase):
    symbol: str


@dataclass(frozen=True)
class PlaceOrder(EffectBase):
    symbol: str
    side: str
    qty: int


@dataclass(frozen=True)
class Position(EffectBase):
    symbol: str


@do
def query_price(symbol):
    return (yield QuotePrice(symbol))


@do
def submit_order(symbol, side, qty):
    return (yield PlaceOrder(symbol, side, qty))


query_price_tool = McpToolDef(
    name="query-price-tool",
    description="Get current price for a symbol",
    params=(McpParamSchema(name="symbol", type="string", description="Stock symbol"),),
    handler=query_price,
)
submit_order_tool = McpToolDef(
    name="submit-order-tool",
    description="Submit a trading order",
    params=(
        McpParamSchema(name="symbol", type="string", description="Stock symbol"),
        McpParamSchema(name="side", type="string", description="Order side", enum=("buy", "sell")),
        McpParamSchema(name="qty", type="integer", description="Quantity"),
    ),
    handler=submit_order,
)

# A paper broker: fixed prices, and a book of positions it keeps.
PRICES = {"7203": 2480}
book: dict[str, int] = {}


@do
def paper_broker(effect, k):
    if isinstance(effect, QuotePrice):
        log("paper_broker answers", effect)
        return (yield Resume(k, PRICES[effect.symbol]))
    if isinstance(effect, PlaceOrder):
        book[effect.symbol] = book.get(effect.symbol, 0) + (effect.qty if effect.side == "buy" else -effect.qty)
        log("paper_broker answers", effect)
        return (yield Resume(k, f"filled: {effect.side} {effect.qty} of {effect.symbol} at {PRICES[effect.symbol]}"))
    if isinstance(effect, Position):
        log("paper_broker answers", effect)
        return (yield Resume(k, book.get(effect.symbol, 0)))
    yield Pass(effect, k)


# The terminal Claude Code would run in: it shows the ready footer, so the prompt is typed at once.
class FakeTerminal:
    def __init__(self):
        self.sessions = {}

    def has_session(self, name):
        return name in self.sessions

    def new_session(self, cfg):
        log("terminal: new session", cfg.session_name)
        self.sessions[cfg.session_name] = "%0"
        return SessionInfo(session_name=cfg.session_name, pane_id="%0", created_at=datetime.now(timezone.utc))

    def send_keys(self, target, keys, *, literal=True, enter=True):
        log("terminal: typed", shlex.split(keys)[0] if not literal else repr(keys))

    def capture_pane(self, target, lines=100, *, strip_ansi_codes=True):
        return "> \n  ⏵⏵ bypass permissions on (shift+tab to cycle)"

    def kill_session(self, session):
        log("terminal: killed", session)
        self.sessions.pop(session, None)


def sse_data(resp):
    buf = ""
    while not buf.endswith("\n\n"):
        buf += resp.read(1).decode()
    return next(line.split(":", 1)[1].strip() for line in buf.strip().split("\n") if line.startswith("data:"))


# The agent's side, replayed: what Claude Code does with .mcp.json.
CALLS = [
    ("tools/list", None, None),
    ("tools/call", "query-price-tool", {"symbol": "7203"}),
    ("tools/call", "submit-order-tool", {"symbol": "7203", "side": "hold", "qty": 100}),
    ("tools/call", "submit-order-tool", {"symbol": "7203", "side": "buy", "qty": 100}),
]


def scripted_agent(work_dir, done):
    try:
        config = json.loads((work_dir / ".mcp.json").read_text())
        log("agent reads .mcp.json:", json.dumps(config))
        url = urlparse(config["mcpServers"]["doeff"]["url"])
        stream = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
        stream.request("GET", "/sse")
        resp = stream.getresponse()
        endpoint = sse_data(resp)
        log("agent opens GET /sse, gets endpoint", endpoint.split("?")[0])
        for i, (method, name, arguments) in enumerate(CALLS, start=1):
            params = {} if name is None else {"name": name, "arguments": arguments}
            post = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
            body = {"jsonrpc": "2.0", "id": i, "method": method, "params": params}
            log("agent POST", json.dumps(body))
            post.request("POST", endpoint, json.dumps(body).encode(), {"Content-Type": "application/json"})
            post.getresponse().read()
            post.close()
            answer = json.loads(sse_data(resp))["result"]
            if method == "tools/list":
                log("agent gets tools:", [(t["name"], list(t["inputSchema"]["properties"])) for t in answer["tools"]])
            else:
                log("agent gets", json.dumps({"isError": answer.get("isError"), "text": answer["content"][0]["text"]}))
        stream.close()
    except Exception as exc:
        log("agent failed:", repr(exc))
    finally:
        done.complete(None)


@do
def trading_agent(work_dir):
    log("program yields Launch")
    handle = yield Launch(
        "trader",
        agent_type=AgentType.CLAUDE,
        work_dir=work_dir,
        prompt="Check 7203 price, buy 100 shares if under 2500.",
        mcp_tools=(query_price_tool, submit_order_tool),
        ready_timeout=2.0,
    )
    log("program resumed with", handle)
    done = yield CreateExternalPromise()
    threading.Thread(target=scripted_agent, args=(work_dir, done), name="agent", daemon=True).start()
    yield Wait(done.future)
    log("program yields Stop")
    yield Stop(handle)
    held = yield Position("7203")
    log("program sees position", held)
    return held


order = sys.argv[1] if len(sys.argv) > 1 else "outer"
threading.current_thread().name = "doeff"
T0 = time.monotonic()
with tempfile.TemporaryDirectory() as tmp:
    work_dir = Path(tmp)
    agent = claude_agent_handler(backend=FakeTerminal())
    broker = install(paper_broker)
    if order == "outer":
        log("stack: claude_agent_handler( paper_broker( trading_agent ) )")
        program = agent(broker(trading_agent(work_dir)))
    else:
        log("stack: paper_broker( claude_agent_handler( trading_agent ) )")
        program = broker(agent(trading_agent(work_dir)))
    result = run(scheduled(state()(program)))
    log("run returns", result)
