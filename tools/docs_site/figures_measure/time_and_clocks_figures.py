"""Writes the three click-through figures of docs/articles/time-and-clocks.md (docs/assets/figures/<name>.html):
paper-passes-tick-on (Layer 1: paper passes Tick on to recorded and answers Order itself), virtual-clock-jumps (A clock is a
handler and Layer 5: the virtual clock parks the waiting task and jumps the time) and wall-clock-waits (Layer 6: the same stack on
the wall clock). The code in the figures is the article's (examples/effects_tour/trading.py), and the values and counts are the
ones time_and_clocks.py in this directory prints.

Usage (from the repository root): uv run --no-project python tools/docs_site/figures_measure/time_and_clocks_figures.py
"""

from __future__ import annotations

from yield_and_async_figures import co, end, fn, kw, line, ring, st, step, write

NL = "\n"
NONE = kw("None")
# Strings of the code that sit inside f-strings below (Python 3.11 cannot reuse an f-string's quote inside it).
BUY = st('"buy"')
SELL = st('"sell"')
LOG_BUY = st('f"t={bar.t} buy  {price:.2f}"')
LOG_SELL = st('f"t={bar.t} sell {price:.2f}"')
SPACES = st('"   "')
STAMP = st('f"    {now:%H:%M} {effect.line}"')


def code(lines: list[str], indent: int = 8) -> str:
    """The lines of a block of code, one per line of the written HTML."""
    return (NL + " " * indent).join(lines)


# --- the strategy, as the article shows it -------------------------------------------------------------------------------

SMA_CROSS = [
    line(None, kw("@do")),
    line(None, f"{kw('def')} {fn('sma_cross')}(fast: int, slow: int):"),
    line(None, "    closes: list[float] = []"),
    line(None, "    position = 0"),
    line(None, "    cash = 0.0"),
    line("s-tick", f"    {kw('while')} (bar := ({kw('yield')} Tick())) {kw('is not')} {NONE}:"),
    line(None, "        closes.append(bar.close)"),
    line(None, f"        {kw('if')} len(closes) &lt; slow:"),
    line(None, f"            {kw('continue')}"),
    line(None, "        f = sum(closes[-fast:]) / fast"),
    line(None, "        s = sum(closes[-slow:]) / slow"),
    line(None, f"        {kw('if')} f &gt; s {kw('and')} position == 0:"),
    line("s-buy", f"            price = {kw('yield')} Order({BUY}, 1)"),
    line(None, "            position, cash = 1, cash - price"),
    line("s-log", f"            {kw('yield')} Log({LOG_BUY})"),
    line(None, f"        {kw('elif')} f &lt; s {kw('and')} position == 1:"),
    line(None, f"            price = {kw('yield')} Order({SELL}, 1)"),
    line(None, "            position, cash = 0, cash + price"),
    line(None, f"            {kw('yield')} Log({LOG_SELL})"),
    line(None, f"    {kw('if')} position:"),
    line(None, f"        cash += {kw('yield')} Order({SELL}, 1)"),
    line(None, f"    {kw('return')} round(cash, 2)"),
]

# --- paper-passes-tick-on: printed(recorded(BARS)(paper(0.0005)(sma_cross(5, 20)))) ----------------------------------------

PAPER_CORE = (
    '<div class="sf-core" data-anchor="core">'
    '<div class="sf-core-head"><span>sma_cross(5, 20)</span><span>the strategy</span></div>'
    '<div class="sf-vars"><span>bar = <span data-slot="bar"></span></span>'
    '<span>price = <span data-slot="price"></span></span></div>'
    f'<div class="sf-code">{"".join(SMA_CROSS)}</div>'
    "</div>"
)
PAPER_HANDLERS = [
    line(None, f"{kw('def')} {fn('paper')}(slippage: float):"),
    line(None, f"    last = [{NONE}]"),
    line(None, ""),
    line(None, f"    {kw('@handler')}"),
    line("p-def", f"    {kw('def')} {fn('fill')}(effect: Tick | Order, k):"),
    line(None, f"        {kw('if')} isinstance(effect, Tick):"),
    line("p-yield", f"            bar = {kw('yield')} effect  {co('# the data feed outside answers')}"),
    line("p-last", f"            last[0] = bar.close {kw('if')} bar {kw('else')} last[0]"),
    line("p-resume", f"            {kw('return')} ({kw('yield')} Resume(k, bar))"),
    line(
        "p-price",
        f"        price = last[0] * (1 + slippage {kw('if')} effect.side == {BUY} "
        f"{kw('else')} 1 - slippage)",
    ),
    line("p-fill", f"        {kw('return')} ({kw('yield')} Resume(k, price))"),
    line(None, ""),
    line(None, f"{kw('def')} {fn('recorded')}(bars: list[Bar]):"),
    line(None, "    it = iter(bars)"),
    line(None, ""),
    line(None, f"    {kw('@handler')}"),
    line("r-def", f"    {kw('def')} {fn('tick')}(effect: Tick, k):"),
    line("r-resume", f"        {kw('return')} ({kw('yield')} Resume(k, next(it, {NONE})))"),
    line(None, ""),
    line(None, kw("@handler")),
    line("pr-def", f"{kw('def')} {fn('printed')}(effect: Log, k):"),
    line("pr-print", f"    print({SPACES}, effect.line)"),
    line(None, f"    {kw('return')} ({kw('yield')} Resume(k, {NONE}))"),
]
PAPER_NAME = 'paper(0.0005) · last close: <span data-slot="last"></span>'
PAPER_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-rings-and-code">
    <div class="sf-rings">
      {ring("printed", "printed", ring("recorded", "recorded(BARS) · 300 bars", ring("paper", PAPER_NAME, PAPER_CORE)))}
    </div>
    <section class="sf-panel is-handler is-wrap" data-panel="handlers">
      <div class="sf-head"><span>the handlers</span><span>from trading.py</span></div>
      <div class="sf-code">
        {code(PAPER_HANDLERS)}
      </div>
      <div class="sf-screen" data-anchor="screen"><span>the screen</span><code data-slot="screen"></code></div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="tick">Tick()</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of sma_cross</small></div>
  <div class="sf-token is-value" data-token="bar">Bar(t=0, close=101.34)</div>
  <div class="sf-token is-effect" data-token="order">Order("buy", 1)</div>
  <div class="sf-token is-k" data-token="k2">k <small>the rest of sma_cross</small></div>
  <div class="sf-token is-value" data-token="price">102.49122</div>
  <div class="sf-token is-effect" data-token="log">Log("t=20 buy  102.49")</div>
</div>"""

HOLDS = {"m-paper": "is-on"}
PAPER_STEPS = [
    step(
        "<code>sma_cross</code> yields <code>Tick()</code>. The nearest handler of <code>Tick</code> is <code>paper</code>, "
        "and it gets the strategy's <code>k</code> with it.",
        now=["s-tick"],
        marks=HOLDS,
        values={
            "call": "run(printed(recorded(BARS)(paper(0.0005)(sma_cross(5, 20)))))",
            "result": "",
            "bar": "",
            "price": "",
            "last": "None",
            "screen": "",
        },
        tokens={"tick": {"from": end("s-tick"), **end("n-paper")}, "k": {"from": end("s-tick"), "at": "m-paper"}},
    ),
    step(
        "<code>paper</code> yields the same <code>Tick</code> again, so it goes on out to <code>recorded</code>.",
        active="handlers",
        now=["p-def", "p-yield"],
        marks=HOLDS,
        tokens={"tick": {"from": end("n-paper"), **end("n-recorded")}, "k": {"at": "m-paper"}},
    ),
    step(
        "<code>recorded</code> answers with the first bar, back to <code>paper</code>, the code that asked.",
        active="handlers",
        now=["r-resume", "p-yield"],
        marks=HOLDS,
        tokens={"bar": {"from": end("n-recorded"), **end("n-paper")}, "k": {"at": "m-paper"}},
    ),
    step(
        "<code>paper</code> writes down the close. It needs it to fill an <code>Order</code> later.",
        active="handlers",
        now=["p-last"],
        marks=HOLDS,
        values={"last": "101.34"},
        tokens={"bar": end("n-paper"), "k": {"at": "m-paper"}},
    ),
    step(
        "<code>paper</code> resumes the strategy's <code>k</code> with the bar. That <code>k</code> is now used.",
        active="handlers",
        now=["p-resume", "s-tick"],
        values={"bar": "Bar(t=0, close=101.34)"},
        tokens={
            "bar": {"from": end("n-paper"), **end("s-tick"), "merge": True},
            "k": {"from": "m-paper", **end("s-tick"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        'At bar 20 the fast average is above the slow one: <code>Order("buy", 1)</code>.',
        now=["s-buy"],
        marks=HOLDS,
        values={"bar": "Bar(t=20, close=102.44)", "last": "102.44"},
        tokens={"order": {"from": end("s-buy"), **end("n-paper")}, "k2": {"from": end("s-buy"), "at": "m-paper"}},
    ),
    step(
        "<code>paper</code> answers <code>Order</code> itself, at the last close plus 0.05%: 102.44 × 1.0005. "
        "<code>recorded</code> never sees it.",
        active="handlers",
        now=["p-price", "p-fill", "s-buy"],
        marks={"r-recorded": "is-skip"},
        values={"price": "102.49122"},
        tokens={
            "price": {"from": end("n-paper"), **end("s-buy"), "merge": True},
            "k2": {"from": "m-paper", **end("s-buy"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        "<code>Log</code> passes <code>paper</code> and <code>recorded</code>, and <code>printed</code> writes the line.",
        active="handlers",
        now=["s-log", "pr-def", "pr-print"],
        marks={"screen": "is-on"},
        values={"screen": "    t=20 buy  102.49"},
        tokens={"log": {"from": end("s-log"), "via": [end("n-paper"), end("n-recorded")], **end("n-printed")}},
    ),
]

# --- virtual-clock-jumps: scheduled(virtual_clock(OPEN)(stamped(timed_backtest(BARS, 60)))) --------------------------------

CLOCK_CORE = (
    '<div class="sf-core" data-anchor="core">'
    '<div class="sf-core-head"><span>sma_cross(5, 20)</span><span>the strategy</span></div>'
    '<div class="sf-code">'
    + line("s-tick", f"{kw('while')} (bar := ({kw('yield')} Tick())) {kw('is not')} {NONE}:")
    + line(None, co("    # … the averages, Order"))
    + line("s-log", f"    {kw('yield')} Log({LOG_BUY})")
    + "</div></div>"
)
CLOCK_RINGS = ring(
    "sched",
    "scheduled · its tasks wait here",
    ring(
        "clock",
        "virtual_clock(OPEN)",
        ring(
            "stamped",
            "stamped",
            ring(
                "recorded",
                "recorded(BARS)",
                ring("paper", "paper(0.0005)", ring("finished", "finished_at", ring("bar", "bar_clock(60)", CLOCK_CORE))),
            ),
        ),
    ),
)
CLOCK_STATE = (
    '<div class="sf-clock">'
    '<div class="sf-vars"><span>now = <span data-slot="time"></span></span>'
    '<span>sleepers = <span data-slot="heap"></span></span></div>'
    '<div class="sf-chips"><span>tasks:</span>'
    '<span class="sf-chip" data-anchor="task-main">the strategy · <span data-slot="main"></span></span>'
    '<span class="sf-chip" data-anchor="task-adv">advance · idle · <span data-slot="adv"></span></span>'
    "</div>"
    '<div class="sf-screen is-on" data-anchor="screen"><span>stamped prints</span><code data-slot="screen"></code></div>'
    "</div>"
)
CLOCK_CODE = [
    line(None, f"{kw('def')} {fn('bar_clock')}(seconds: float):"),
    line(None, f"    {kw('@handler')}"),
    line("b-def", f"    {kw('def')} {fn('wait_for_bar')}(effect: Tick, k):"),
    line("b-delay", f"        {kw('yield')} Delay(seconds)  {co('# the clock outside decides what waiting means')}"),
    line("b-pass", f"        {kw('return')} ({kw('yield')} Resume(k, ({kw('yield')} effect)))"),
    line(None, ""),
    line(None, f"{kw('def')} {fn('virtual_clock')}(start: datetime):"),
    line(None, "    now = start"),
    line(None, f"    sleepers = []  {co('# (wake-up time, order, promise), earliest first')}"),
    line(None, "    order = count()"),
    line(None, f"    advancing = {kw('False')}"),
    line(None, ""),
    line(None, f"    {kw('@do')}"),
    line("a-def", f"    {kw('def')} {fn('advance')}():  {co('# idle priority: runs while every other task waits')}"),
    line(None, f"        {kw('nonlocal')} now, advancing"),
    line("a-while", f"        {kw('while')} sleepers:"),
    line("a-pop", "            now, _, promise = heapq.heappop(sleepers)"),
    line("a-complete", f"            {kw('yield')} CompletePromise(promise, {NONE})"),
    line("a-done", f"        advancing = {kw('False')}"),
    line(None, ""),
    line(None, f"    {kw('@handler')}"),
    line("v-def", f"    {kw('def')} {fn('clock')}(effect: Delay | GetTime, k):"),
    line(None, f"        {kw('nonlocal')} advancing"),
    line("v-if-get", f"        {kw('if')} isinstance(effect, GetTime):"),
    line("v-get", f"            {kw('return')} ({kw('yield')} Resume(k, now))"),
    line("v-create", f"        promise = {kw('yield')} CreatePromise()"),
    line(
        "v-push",
        "        heapq.heappush(sleepers, (now + timedelta(seconds=effect.seconds), next(order), promise))",
    ),
    line("v-if", f"        {kw('if not')} advancing:"),
    line("v-adv", f"            advancing = {kw('True')}"),
    line("v-spawn", f"            {kw('yield')} Spawn(advance(), priority=TaskPriority.IDLE, daemon={kw('True')})"),
    line("v-wait", f"        {kw('yield')} Wait(promise.future)"),
    line("v-resume", f"        {kw('return')} ({kw('yield')} Resume(k, {NONE}))"),
    line(None, ""),
    line(None, kw("@handler")),
    line("st-def", f"{kw('def')} {fn('stamped')}(effect: Log, k):"),
    line("st-get", f"    now = {kw('yield')} GetTime()"),
    line("st-print", f"    print({STAMP})"),
    line(None, f"    {kw('return')} ({kw('yield')} Resume(k, {NONE}))"),
]
CLOCK_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-state-and-code">
    <div>
      <div class="sf-rings">
        {CLOCK_RINGS}
      </div>
      {CLOCK_STATE}
    </div>
    <section class="sf-panel is-handler is-wrap" data-panel="handlers">
      <div class="sf-head"><span>the clock layers</span><span>from trading.py</span></div>
      <div class="sf-code">
        {code(CLOCK_CODE)}
      </div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="tick">Tick()</div>
  <div class="sf-token is-effect" data-token="delay">Delay(60)</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of bar_clock</small></div>
  <div class="sf-token is-value" data-token="bar">Bar(t=0, close=101.34)</div>
  <div class="sf-token is-effect" data-token="delay2">Delay(60)</div>
  <div class="sf-token is-k" data-token="k2">k <small>the rest of bar_clock</small></div>
  <div class="sf-token is-effect" data-token="log">Log(…)</div>
  <div class="sf-token is-effect" data-token="get">GetTime()</div>
  <div class="sf-token is-value" data-token="at">09:21</div>
  <div class="sf-token is-effect" data-token="get2">GetTime()</div>
  <div class="sf-token is-value" data-token="ended">14:01</div>
</div>"""

OUTWARD = [end("n-finished"), end("n-paper"), end("n-recorded"), end("n-stamped")]
NO_ADVANCE = {"task-adv": "is-off"}
CLOCK_STEPS = [
    step(
        "<code>bar_clock</code> takes <code>Tick</code> first. Before passing it on, it yields <code>Delay(60)</code> of its "
        "own.",
        active="handlers",
        now=["s-tick", "b-def", "b-delay"],
        marks={**NO_ADVANCE, "m-bar": "is-on"},
        values={
            "call": "run(scheduled(virtual_clock(OPEN)(stamped(timed_backtest(BARS, 60)))))",
            "result": "",
            "time": "09:00",
            "heap": "[]",
            "main": "runs",
            "adv": "",
            "screen": "",
        },
        tokens={"tick": {"from": end("s-tick"), "at": "m-bar"}, "delay": end("n-bar")},
    ),
    step(
        "No layer in between handles <code>Delay</code>, so it goes out to the clock, with the <code>k</code> of "
        "<code>bar_clock</code>.",
        active="handlers",
        now=["v-def"],
        marks={**NO_ADVANCE, "m-bar": "is-on", "m-clock": "is-on"},
        tokens={
            "tick": {"at": "m-bar"},
            "delay": {"from": end("n-bar"), "via": OUTWARD, **end("n-clock")},
            "k": {"from": end("n-bar"), "via": OUTWARD, "at": "m-clock", "delay": 0.3},
        },
    ),
    step(
        "The clock creates a promise and puts it on the heap, to wake at 09:01.",
        active="handlers",
        now=["v-create", "v-push"],
        marks={**NO_ADVANCE, "m-bar": "is-on", "m-clock": "is-on"},
        values={"heap": "[(09:01, promise)]"},
        tokens={"tick": {"at": "m-bar"}, "k": {"at": "m-clock"}},
    ),
    step(
        "No <code>advance</code> task runs yet, so the clock spawns one at idle priority.",
        active="handlers",
        now=["v-if", "v-adv", "v-spawn"],
        marks={"m-bar": "is-on", "m-clock": "is-on", "task-adv": "is-new"},
        values={"adv": "waits its turn"},
        tokens={"tick": {"at": "m-bar"}, "k": {"at": "m-clock"}},
    ),
    step(
        "<code>Wait</code> parks the strategy's task in the scheduler, together with its <code>k</code>.",
        active="handlers",
        now=["v-wait"],
        marks={"m-bar": "is-on", "m-sched": "is-on", "core": "is-parked", "task-main": "is-parked"},
        values={"main": "waits"},
        tokens={"tick": {"at": "m-bar"}, "k": {"from": "m-clock", "at": "m-sched"}},
    ),
    step(
        "Every other task waits, so <code>advance</code> runs. It pops 09:01, sets the time and completes the promise.",
        active="handlers",
        now=["a-while", "a-pop", "a-complete"],
        marks={"m-bar": "is-on", "m-sched": "is-on", "core": "is-parked", "task-main": "is-parked", "task-adv": "is-now"},
        values={"time": "09:01", "heap": "[]", "adv": "runs"},
        tokens={"tick": {"at": "m-bar"}, "k": {"at": "m-sched"}},
    ),
    step(
        "The task wakes first. The clock resumes its <code>k</code>, and <code>bar_clock</code> passes the "
        "<code>Tick</code> on to the feed.",
        active="handlers",
        now=["v-resume", "b-pass", "s-tick"],
        values={"main": "runs", "adv": "waits its turn"},
        tokens={
            "k": {"from": "m-sched", **end("n-bar"), "merge": True},
            "tick": {"from": "m-bar", "via": [end("n-paper")], **end("n-recorded"), "delay": 0.7},
            "bar": {"from": end("n-recorded"), "via": [end("n-paper"), end("n-bar")], **end("s-tick"), "merge": True, "delay": 2.0},
        },
    ),
    step(
        "The next <code>Tick</code> puts 09:02 on the heap. <code>advance</code> is still running, so no new task starts.",
        active="handlers",
        now=["s-tick", "b-delay", "v-push", "v-if"],
        marks={"m-clock": "is-on"},
        values={"heap": "[(09:02, promise)]"},
        tokens={
            "delay2": {"from": end("n-bar"), "via": OUTWARD, **end("n-clock")},
            "k2": {"from": end("n-bar"), "via": OUTWARD, "at": "m-clock", "delay": 0.3},
        },
    ),
    step(
        "<code>stamped</code> yields <code>GetTime</code> while it handles <code>Log</code>. The clock says 09:21.",
        active="handlers",
        now=["s-log", "st-def", "st-get", "v-if-get", "v-get", "st-print"],
        values={"time": "09:21", "heap": "[]", "screen": "    09:21 t=20 buy  102.49"},
        tokens={
            "log": {"from": end("s-log"), "via": [end("n-bar"), end("n-paper")], **end("n-stamped")},
            "get": {"from": end("n-stamped"), **end("n-clock"), "delay": 1.3},
            "at": {"from": end("n-clock"), **end("n-stamped"), "merge": True, "delay": 2.0},
        },
    ),
    step(
        "The last <code>Tick</code> waits its minute too and gets <code>None</code>. Five hours and a minute of market "
        "time pass in well under a second.",
        active="handlers",
        now=["a-done", "v-get"],
        marks={"task-adv": "is-done"},
        values={
            "time": "14:01",
            "heap": "[]",
            "adv": "done",
            "screen": "    12:53 t=232 buy  127.63",
            "result": "pnl 50.79 at 14:01",
        },
        tokens={
            "get2": {"from": end("n-finished"), "via": [end("n-paper"), end("n-recorded"), end("n-stamped")], **end("n-clock")},
            "ended": {
                "from": end("n-clock"),
                "via": [end("n-stamped"), end("n-recorded"), end("n-paper")],
                **end("n-finished"),
                "merge": True,
                "delay": 2.0,
            },
        },
    ),
]

# --- wall-clock-waits: the stack of Layer 6 on the virtual clock and on the wall clock -------------------------------------


def stack(side: str, outside: str) -> str:
    """One column's stack, from the clock in: quiet, recorded, paper, finished_at, bar_clock and the strategy."""
    core = (
        f'<div class="sf-core" data-anchor="{side}-core">'
        '<div class="sf-core-head"><span>sma_cross(5, 20)</span><span>unchanged</span></div>'
        "</div>"
    )
    inner = ring(
        f"{side}-quiet",
        "quiet",
        ring(
            f"{side}-recorded",
            "recorded(BARS[:60])",
            ring(f"{side}-paper", "paper(0.0005)", ring(f"{side}-finished", "finished_at", ring(f"{side}-bar", "bar_clock(0.01)", core))),
        ),
    )
    return ring(f"{side}-sched", "scheduled", outside.replace("INNER", inner))


VIRTUAL = ring("L-clock", "virtual_clock(OPEN)", "INNER")
LOOP = (
    '<div class="sf-loop" data-anchor="R-loop">'
    '<div class="sf-loop-head" data-anchor="R-loop-head">an asyncio loop: <span data-slot="loop"></span></div>'
    "</div>"
)
WALL = ring(
    "R-await",
    "await_handler",
    LOOP + ring("R-wall", "wall_clock", ring("R-virtual", "virtual_clock(OPEN)", "INNER")),
)
WALL_CODE = [
    line(None, kw("@handler")),
    line("w-def", f"{kw('def')} {fn('wall_clock')}(effect: Delay | GetTime, k):"),
    line(None, f"    {kw('if')} isinstance(effect, GetTime):"),
    line(None, f"        {kw('return')} ({kw('yield')} Resume(k, datetime.now(timezone.utc)))"),
    line("w-await", f"    {kw('yield')} Await(asyncio.sleep(effect.seconds))"),
    line("w-resume", f"    {kw('return')} ({kw('yield')} Resume(k, {NONE}))"),
]


def tally(side: str) -> str:
    """Under one column's stack: how long it really waited, its pnl, and the effects that reached its clock layers."""
    return (
        f'<div class="sf-vars"><span>real wait: <span data-slot="{side}-wait"></span></span>'
        f'<span>pnl: <span data-slot="{side}-pnl"></span></span></div>'
        f'<div class="sf-effects" data-anchor="{side}-effects"><span>what bar_clock and the strategy yield</span>'
        f'<code data-slot="{side}-effects"></code></div>'
    )


SWAP_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-two-stacks">
    <section class="sf-panel is-program" data-panel="virtual">
      <div class="sf-head"><span>on the virtual clock</span><span>Layer 5</span></div>
      <div class="sf-rings">
        {stack("L", VIRTUAL)}
      </div>
      {tally("L")}
    </section>
    <section class="sf-panel is-handler is-wrap" data-panel="wall">
      <div class="sf-head"><span>on the wall clock</span><span>Layer 6</span></div>
      <div class="sf-rings">
        {stack("R", WALL)}
      </div>
      {tally("R")}
      <div class="sf-code">
        {code(WALL_CODE)}
      </div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="dL">Delay(0.01)</div>
  <div class="sf-token is-effect" data-token="dR">Delay(0.01)</div>
  <div class="sf-token is-k" data-token="kR">k <small>the rest of bar_clock</small></div>
  <div class="sf-token is-effect" data-token="aw">Await(asyncio.sleep(0.01))</div>
</div>"""

SWAPPED = {"r-R-virtual": "is-off"}
INWARD_L = [end("n-L-finished"), end("n-L-paper"), end("n-L-recorded"), end("n-L-quiet")]
INWARD_R = [end("n-R-finished"), end("n-R-paper"), end("n-R-recorded"), end("n-R-quiet")]
EFFECTS = "Delay Tick Delay Tick … Order Log … Delay Tick: 61 Delay, 61 Tick, 2 Order, 2 Log"
SWAP_STEPS = [
    step(
        "Take out <code>virtual_clock(OPEN)</code>, and put <code>wall_clock</code> and <code>await_handler</code> in its "
        "place. The layers inside are the same on both sides.",
        active="wall",
        marks={**SWAPPED, "r-R-wall": "is-new", "r-R-await": "is-new"},
        values={
            "call": "timed_backtest(BARS[:60], 0.01): 60 bars, 10 ms apart",
            "result": "",
            "loop": "",
            "L-wait": "",
            "R-wait": "",
            "L-pnl": "",
            "R-pnl": "",
            "L-effects": "",
            "R-effects": "",
        },
    ),
    step(
        "<code>bar_clock</code> yields the same <code>Delay(0.01)</code>. Nothing inside the clock changed.",
        marks=SWAPPED,
        tokens={"dL": end("n-L-bar"), "dR": end("n-R-bar")},
    ),
    step(
        "<code>wall_clock</code> answers <code>Delay</code> by yielding <code>Await(asyncio.sleep(0.01))</code>.",
        active="wall",
        now=["w-def", "w-await"],
        marks={**SWAPPED, "m-R-wall": "is-on"},
        tokens={
            "dL": {"from": end("n-L-bar"), "via": INWARD_L, **end("n-L-clock")},
            "dR": {"from": end("n-R-bar"), "via": INWARD_R, **end("n-R-wall")},
            "kR": {"from": end("n-R-bar"), "via": INWARD_R, "at": "m-R-wall", "delay": 0.3},
            "aw": {"from": end("n-R-wall"), **end("n-R-await"), "delay": 3.0},
        },
    ),
    step(
        "<code>await_handler</code> runs the sleep on an asyncio loop. 10 ms later, <code>wall_clock</code> resumes this "
        "<code>k</code>.",
        active="wall",
        now=["w-resume"],
        marks={**SWAPPED, "m-R-wall": "is-on"},
        values={"loop": "asyncio.sleep(0.01), 10 ms"},
        tokens={
            "aw": {"from": end("n-R-await"), **end("R-loop-head"), "merge": True},
            "kR": {"from": "m-R-wall", "via": INWARD_R, **end("n-R-bar"), "merge": True, "delay": 1.0},
        },
    ),
    step(
        "Sixty bars and sixty-one waits: the wall clock takes over 0.6 seconds. The virtual clock jumps through the same "
        "0.61 s of its own time.",
        marks=SWAPPED,
        values={"L-wait": "none, 0.61 s on its clock", "R-wait": "over 0.6 s", "loop": "61 sleeps"},
    ),
    step(
        "Both stacks see the same effects, in the same order.",
        marks={**SWAPPED, "L-effects": "is-same", "R-effects": "is-same"},
        values={"L-effects": EFFECTS, "R-effects": EFFECTS},
    ),
    step(
        "So both give pnl 6.85. The strategy never asked which clock was outside.",
        marks={**SWAPPED, "L-core": "is-same", "R-core": "is-same"},
        values={"L-pnl": "6.85", "R-pnl": "6.85", "result": "6.85 and 6.85"},
    ),
]

if __name__ == "__main__":
    write(
        "paper-passes-tick-on",
        'The section "Layer 1: a backtest over recorded bars" of docs/articles/time-and-clocks.md, one step per click: paper '
        "passes each Tick on to recorded and keeps the close, and answers Order itself.\n"
        "     Written by tools/docs_site/figures_measure/time_and_clocks_figures.py; the values are what time_and_clocks.py "
        "prints.",
        PAPER_STAGE,
        PAPER_STEPS,
    )
    write(
        "virtual-clock-jumps",
        'The sections "A clock is a handler" and "Layer 5: a virtual clock" of docs/articles/time-and-clocks.md, one step per '
        "click: bar_clock's Delay(60) reaches virtual_clock, which parks the task, and the idle advance task jumps the time.\n"
        "     Written by tools/docs_site/figures_measure/time_and_clocks_figures.py; the counts are what time_and_clocks.py "
        "prints.",
        CLOCK_STAGE,
        CLOCK_STEPS,
    )
    write(
        "wall-clock-waits",
        'The section "Layer 6: the wall clock" of docs/articles/time-and-clocks.md, one step per click: with wall_clock and '
        "await_handler in the virtual clock's place, the same Delay becomes an asyncio sleep and the result is the same.\n"
        "     Written by tools/docs_site/figures_measure/time_and_clocks_figures.py; the counts are what time_and_clocks.py "
        "prints.",
        SWAP_STAGE,
        SWAP_STEPS,
    )
