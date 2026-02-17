# Domain Angle: Trading Strategy — Backtest / Paper / Live Handler Switching

Trading is the canonical example of "same logic, different time semantics." The strategy is a Program. Whether it runs against historical data in milliseconds or live market data in real-time is a handler decision.

## Why This Is NOT Just DI

DI can swap a `MarketDataService` from real to historical. What DI **cannot** do:

1. **Swap time itself** — `Delay(5.0)` sleeps 5 real seconds in live mode but advances instantly in backtest mode
2. **Coordinate time, events, and concurrency** — scheduled events fire at simulated time, not wall-clock time
3. **Replay a live session as a backtest** — same recording, simulated time, instant execution
4. **Run the same strategy across 10 years of data in seconds** — impossible if `Delay` actually sleeps

The key insight from [docs/20-why-effects-over-di.md](../20-why-effects-over-di.md): time, events, and concurrency are **independent effect domains** that compose through handlers. DI can't keep them independent — you'd build a monolithic `SimulationEngine`.

## The Strategy: Pure Effects

```python
from doeff import do, Program, Get, Put, Tell, Safe
from doeff.effects import Await, Spawn, Gather

@dataclass(frozen=True)
class GetPrice(Effect):
    symbol: str

@dataclass(frozen=True)
class PlaceOrder(Effect):
    symbol: str
    side: str          # "buy" | "sell"
    quantity: float
    order_type: str    # "market" | "limit"
    limit_price: float | None = None

@dataclass(frozen=True)
class GetPortfolio(Effect):
    pass

@dataclass(frozen=True)
class Delay(Effect):
    seconds: float

@dataclass(frozen=True)
class GetTime(Effect):
    pass

@dataclass(frozen=True)
class ScheduleAt(Effect):
    time: float
    program: Program

@do
def mean_reversion_strategy(symbol: str, window: int = 20) -> Program[None]:
    """Simple mean reversion: buy below SMA, sell above."""
    prices = []

    while True:
        price = yield GetPrice(symbol)
        now = yield GetTime()
        prices.append(price)
        yield Tell({"time": now, "price": price, "symbol": symbol})

        if len(prices) >= window:
            sma = sum(prices[-window:]) / window
            portfolio = yield GetPortfolio()
            position = portfolio.get(symbol, 0)

            if price < sma * 0.98 and position == 0:
                yield Tell(f"BUY signal: {price:.2f} < SMA {sma:.2f}")
                yield PlaceOrder(symbol, "buy", quantity=100, order_type="market")

            elif price > sma * 1.02 and position > 0:
                yield Tell(f"SELL signal: {price:.2f} > SMA {sma:.2f}")
                yield PlaceOrder(symbol, "sell", quantity=position, order_type="market")

        yield Delay(60.0)  # wait 1 minute between checks
```

**The strategy has zero awareness of whether it's running against historical data or live markets.** `GetPrice`, `PlaceOrder`, `Delay`, `GetTime` — all effects.

## Handler Mode 1: Backtest (10 Years in Seconds)

```python
def backtest_market_handler(historical_data):
    """Feeds historical price data. Simulates order fills."""
    prices = iter(historical_data)

    def handler(effect, k):
        if isinstance(effect, GetPrice):
            try:
                return next(prices)
            except StopIteration:
                raise BacktestComplete()

        elif isinstance(effect, PlaceOrder):
            # Simulate fill at current price (no slippage for simplicity)
            current_price = yield GetPrice(effect.symbol)
            portfolio = yield Get("portfolio")
            if effect.side == "buy":
                portfolio[effect.symbol] = portfolio.get(effect.symbol, 0) + effect.quantity
                yield Put("cash", (yield Get("cash")) - current_price * effect.quantity)
            else:
                portfolio[effect.symbol] = portfolio.get(effect.symbol, 0) - effect.quantity
                yield Put("cash", (yield Get("cash")) + current_price * effect.quantity)
            yield Put("portfolio", portfolio)
            return OrderResult(filled=True, price=current_price)

        elif isinstance(effect, GetPortfolio):
            return (yield Get("portfolio"))

        yield Delegate()
    return handler

def simulated_time_handler(start_time):
    """Delay advances simulated clock instantly. GetTime returns simulated time."""
    sim_time = start_time

    def handler(effect, k):
        nonlocal sim_time
        if isinstance(effect, Delay):
            sim_time += effect.seconds  # instant — no real sleep
            return None
        elif isinstance(effect, GetTime):
            return sim_time
        yield Delegate()
    return handler

# Backtest: 10 years of minute data -> runs in seconds
result = run(
    mean_reversion_strategy("AAPL", window=20),
    handlers=[
        backtest_market_handler(load_csv("AAPL_2015_2025.csv")),
        simulated_time_handler(start_time=datetime(2015, 1, 1).timestamp()),
    ],
    store={"portfolio": {}, "cash": 100_000},
)

# result.writer_output contains every price check, every trade signal
# Instant. Deterministic. Reproducible.
```

**`Delay(60.0)` advances the simulated clock by 60 seconds instantly.** With DI, `await asyncio.sleep(60)` would actually sleep for 60 seconds. You'd need to special-case every `sleep` call. With effects, the handler controls time.

## Handler Mode 2: Paper Trading (Real Data, Simulated Execution)

```python
def live_market_handler():
    """Real price feeds, but orders don't execute."""
    def handler(effect, k):
        if isinstance(effect, GetPrice):
            return fetch_real_price(effect.symbol)  # real market data

        elif isinstance(effect, PlaceOrder):
            # Log the order but don't execute
            yield Tell(f"[PAPER] Would {effect.side} {effect.quantity} {effect.symbol}")
            # Simulate fill at current price
            price = yield GetPrice(effect.symbol)
            return OrderResult(filled=True, price=price, paper=True)

        yield Delegate()
    return handler

def real_time_handler():
    """Delay actually sleeps. GetTime returns wall clock."""
    def handler(effect, k):
        if isinstance(effect, Delay):
            import asyncio
            await asyncio.sleep(effect.seconds)  # real wall-clock delay
            return None
        elif isinstance(effect, GetTime):
            import time
            return time.time()
        yield Delegate()
    return handler

# Paper trading: real prices, simulated orders, real time
result = run(
    mean_reversion_strategy("AAPL", window=20),
    handlers=[
        live_market_handler(),
        real_time_handler(),
    ],
    store={"portfolio": {}, "cash": 100_000},
)
```

## Handler Mode 3: Live Trading (Real Everything)

```python
def live_execution_handler(broker_client):
    """Real price feeds, real order execution."""
    def handler(effect, k):
        if isinstance(effect, GetPrice):
            return broker_client.get_quote(effect.symbol)

        elif isinstance(effect, PlaceOrder):
            yield Tell(f"[LIVE] {effect.side} {effect.quantity} {effect.symbol}")
            order = broker_client.place_order(
                symbol=effect.symbol,
                side=effect.side,
                qty=effect.quantity,
                type=effect.order_type,
                limit_price=effect.limit_price,
            )
            return OrderResult(filled=order.filled, price=order.fill_price)

        elif isinstance(effect, GetPortfolio):
            return broker_client.get_positions()

        yield Delegate()
    return handler

# Live trading: real everything, with recording for audit
result = run(
    mean_reversion_strategy("AAPL", window=20),
    handlers=[
        RecordingHandler("trades/live_2026_02_12.json"),  # audit trail
        live_execution_handler(alpaca_client),
        real_time_handler(),
    ],
)
```

## Handler Mode 4: Replay a Live Session as Backtest

```python
# Yesterday's live session, replayed instantly for analysis
result = run(
    mean_reversion_strategy("AAPL", window=20),
    handlers=[
        ReplayHandler("trades/live_2026_02_11.json"),
        simulated_time_handler(start_time=yesterday_start),
    ],
)
# Same decisions, same prices, but runs in milliseconds instead of hours.
# Useful for: analyzing why a trade was made, verifying strategy logic,
# generating reports from recorded data.
```

## Handler Mode 5: What-If Analysis

```python
# "What if I had used a 50-period SMA instead of 20?"
# Replay market data but let the strategy re-decide

result_20 = run(
    mean_reversion_strategy("AAPL", window=20),
    handlers=[
        backtest_market_handler(historical_data),
        simulated_time_handler(start),
    ],
    store={"portfolio": {}, "cash": 100_000},
)

result_50 = run(
    mean_reversion_strategy("AAPL", window=50),  # only this changed
    handlers=[
        backtest_market_handler(historical_data),  # same data
        simulated_time_handler(start),
    ],
    store={"portfolio": {}, "cash": 100_000},
)

# Compare P&L, trade count, drawdown — same market, different parameters
```

## The Handler Stack Summary

```
Same strategy Program, different handler stacks:

  [backtest_market, simulated_time]                 -> 10-year backtest in seconds
  [live_market, real_time]                          -> paper trading (real data, no execution)
  [live_execution, real_time, recording]            -> live trading with audit trail
  [replay, simulated_time]                          -> replay yesterday's session instantly
  [backtest_market, simulated_time, metrics]        -> parameter sweep / optimization
  [live_market, real_time, gradio_streaming]        -> live dashboard with effect trace
```

## Why DI Falls Apart Here

The [simulation case study in docs/20-why-effects-over-di.md](../../20-why-effects-over-di.md) explains this in depth. The summary:

With DI, you'd need:
```python
class TimeService(Protocol): ...
class MarketService(Protocol): ...
class ExecutionService(Protocol): ...
```

Three service implementations per mode (backtest, paper, live). But the services **need to coordinate**: `SimulatedTimeService` must control when `SimulatedMarketService` advances to the next candle. You end up with a monolithic `BacktestEngine` that bundles time + market + execution together. The independence is an illusion.

With effects, each handler manages its own concern:
- `simulated_time_handler`: only handles `Delay` and `GetTime`
- `backtest_market_handler`: only handles `GetPrice` and `PlaceOrder`
- They compose through the program's yield points
- No shared state, no circular references, no coordination protocol

## The Pitch

> "Write your trading strategy once as effects. Backtest 10 years in seconds — because `Delay(60)` advances simulated time instantly. Paper trade with real prices — same strategy, swap the order handler. Go live — swap one more handler, add a recording handler for audit. Replay yesterday's live session for analysis — in milliseconds. The strategy never changes. Only the handlers."

## Gradio Dashboard Integration

Combine with the [Gradio positioning](gradio-prototyping.md) for a live trading dashboard:

```python
result = run(
    mean_reversion_strategy("AAPL"),
    handlers=[
        gradio_streaming_handler,    # live chart, trade log, P&L curve
        live_market_handler(),
        real_time_handler(),
    ],
)
```

Every `GetPrice`, `PlaceOrder`, and `Tell` pushes to the Gradio UI in real-time. The strategy doesn't know about Gradio. The dashboard is an orthogonal handler.
