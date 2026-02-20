# Why Algebraic Effects? Real-World Use Cases vs Dependency Injection

This document captures concrete scenarios where doeff's algebraic effects provide genuine advantages over traditional dependency injection (DI). These aren't theoretical benefits — they emerged from building real multi-provider LLM and image generation systems.

## The Setup: Multi-Provider AI Workflows

Modern AI applications rarely use a single provider. A typical workflow might:

- Use GPT-4o for structured analysis
- Use Gemini for image generation
- Use Claude for code review
- Track costs across all providers
- Retry failed calls with observability
- Switch providers without rewriting business logic

Both effects and DI can solve this. The question is: where does each approach shine?

## Approach 1: Dependency Injection

```python
from typing import Protocol

class LLMService(Protocol):
    async def chat(self, messages: list, model: str, **kwargs) -> Response: ...
    async def structured(self, messages: list, model: str, response_format: type[T]) -> T: ...

class ImageService(Protocol):
    async def generate(self, prompt: str, model: str, **kwargs) -> ImageResult: ...

# Multi-provider router
class MultiLLMService:
    def __init__(self, providers: dict[str, LLMService]):
        self.providers = providers

    async def chat(self, messages, model, **kwargs):
        provider = self._route(model)  # gpt-* -> openai, gemini-* -> gemini
        return await provider.chat(messages, model, **kwargs)

# Usage
@injected
async def my_workflow(llm: LLMService, images: ImageService):
    analysis = await llm.structured(messages, "gpt-4o", CodeAnalysis)
    image = await images.generate("A diagram of the analysis", "seedream-4")
    return analysis, image
```

**DI strengths here:**
- Familiar `async/await` syntax
- IDE autocomplete on `LLMService` methods
- Explicit dependency wiring at construction time
- Strong typing via Protocol

## Approach 2: Algebraic Effects (doeff)

```python
from doeff import do, run, WithHandler
from doeff_llm.effects import LLMChat, LLMStructuredOutput
from doeff_image.effects import ImageGenerate

@do
def my_workflow():
    analysis = yield LLMStructuredOutput(
        messages=[{"role": "user", "content": "Analyze this code"}],
        response_format=CodeAnalysis,
        model="gpt-4o",
    )
    image = yield ImageGenerate(
        prompt="A diagram of the analysis",
        model="seedream-4",
    )
    return analysis, image

# Stack handlers — routing is automatic
result = run(
    WithHandler(WithHandler(WithHandler(
        my_workflow(),
        openai_handler),    # Handles gpt-* models
        gemini_handler),    # Handles gemini-* models
        seedream_handler),  # Handles seedream-* models
    env={"openai_api_key": "...", "gemini_api_key": "...", "seedream_api_key": "..."}
)
```

## Where Effects Genuinely Win

### 1. Cross-Cutting Concerns Compose for Free

This is the single biggest advantage.

**With DI**, every service needs its own middleware chain:

```python
# DI: Each service needs explicit instrumentation
class LoggingLLM(LLMService):
    def __init__(self, inner: LLMService, logger: Logger): ...
    async def chat(self, messages, model, **kwargs):
        logger.info(f"LLM call: {model}")
        result = await self.inner.chat(messages, model, **kwargs)
        logger.info(f"LLM response: {result.usage.total_tokens} tokens")
        return result

class RetryLLM(LLMService):
    def __init__(self, inner: LLMService, max_retries: int = 3): ...

class CostTrackingLLM(LLMService):
    def __init__(self, inner: LLMService, cost_tracker: CostTracker): ...

# Same wrappers needed for ImageService, EmbeddingService, etc.
class LoggingImage(ImageService): ...
class RetryImage(ImageService): ...
class CostTrackingImage(ImageService): ...

# Wiring becomes combinatorial
llm = CostTrackingLLM(RetryLLM(LoggingLLM(OpenAILLM(), logger), 3), tracker)
images = CostTrackingImage(RetryImage(LoggingImage(SeedreamImage(), logger), 3), tracker)
```

**With effects**, cross-cutting handlers apply to ALL effects simultaneously:

```python
@do
def my_workflow():
    yield LLMChat(...)           # Automatically gets: logging, cost tracking,
    yield ImageGenerate(...)     #   retry, tracing, caching — all from
    yield LLMEmbedding(...)      #   orthogonal stacked handlers
    yield GetSecret(...)         # Even non-AI effects get the same benefits

# One cost handler works across all effect types
def cost_cap_handler(effect, k):
    if hasattr(effect, 'model'):  # Any effect with a model field
        current_cost = yield Get("total_cost")
        if current_cost > MAX_BUDGET:
            raise BudgetExceededError(current_cost)
    yield Delegate()

# One retry handler works for everything
def retry_handler(effect, k):
    for attempt in range(3):
        safe = yield Try(Resume(k, effect))
        if safe.is_ok():
            return safe.value
    raise safe.error
```

**Why this matters:** With N services and M cross-cutting concerns:
- DI requires N x M wrapper classes
- Effects require M handlers (regardless of N)

### 2. Effect Interception Without Wrapping

Effects can be intercepted, transformed, or blocked without the original code knowing.

```python
# Budget cap across ALL providers — impossible to bypass
def budget_handler(effect, k):
    if isinstance(effect, (LLMChat, LLMStructuredOutput, ImageGenerate)):
        total = yield Get("total_cost")
        if total > settings.max_budget_usd:
            yield Tell(f"Budget exceeded: ${total:.2f} > ${settings.max_budget_usd}")
            return (yield Resume(k, BudgetExceededResult()))
    yield Delegate()

# Model rewriting — swap models transparently
def model_rewrite_handler(effect, k):
    if isinstance(effect, LLMChat) and effect.model == "gpt-4":
        # Transparently downgrade to cheaper model in dev
        effect = LLMChat(**{**vars(effect), "model": "gpt-4o-mini"})
    yield Delegate()  # Let the actual provider handler deal with it

# Dry-run mode — intercept ALL side effects
def dry_run_handler(effect, k):
    if isinstance(effect, (LLMChat, ImageGenerate)):
        yield Tell(f"[DRY RUN] Would execute: {type(effect).__name__}(model={effect.model})")
        return (yield Resume(k, mock_response(effect)))
    yield Delegate()
```

With DI, each of these requires a new wrapper class per service type.

### 3. The Call Tree Is Automatic

Every `yield` in a `@do` function is captured in the effect call tree. You get a complete execution trace for free.

```python
@do
def rag_pipeline(query: str):
    docs = yield semantic_search(query)       # Traced
    analysis = yield LLMStructuredOutput(     # Traced
        messages=build_prompt(docs),
        model="gpt-4o",
        response_format=Analysis,
    )
    image = yield ImageGenerate(              # Traced
        prompt=analysis.diagram_prompt,
        model="gemini-3-pro-image",
    )
    return PipelineResult(analysis, image)

# After execution, result contains:
# rag_pipeline()
# ├── semantic_search()
# │   └── LLMEmbedding(model="text-embedding-3-small")
# ├── LLMStructuredOutput(model="gpt-4o")
# └── ImageGenerate(model="gemini-3-pro-image")
```

With doeff-flow, this becomes live-observable in real-time. DI gives you nothing comparable without explicit OpenTelemetry instrumentation.

### 4. Handler Stacking IS the Router

No explicit `MultiLLMService` router needed. The handler stack is the routing mechanism.

```python
# Each handler checks model prefix, delegates if not its concern
def openai_handler(effect, k):
    if isinstance(effect, LLMChat) and effect.model.startswith("gpt-"):
        response = yield _call_openai(effect)
        return (yield Resume(k, response))
    yield Delegate()  # Not my model, pass to next handler

def gemini_handler(effect, k):
    if isinstance(effect, LLMChat) and effect.model.startswith("gemini-"):
        response = yield _call_gemini(effect)
        return (yield Resume(k, response))
    yield Delegate()

# Stacking = routing
result = run(
    WithHandler(WithHandler(
        my_workflow(),
        openai_handler),   # Inner: tries first
        gemini_handler),   # Outer: tries if inner delegates
    env={...}
)
```

Adding a new provider? Just stack one more handler. No changes to the router, no factory updates, no wiring changes.

### 5. Environment Portability (Secrets Example)

Secret management perfectly illustrates the handler-swapping advantage:

```python
from doeff_secret.effects import GetSecret

@do
def deploy_workflow():
    db_password = yield GetSecret(secret_id="db-password")
    api_key = yield GetSecret(secret_id="openai-api-key")
    yield do_deployment(db_password, api_key)
```

The program never mentions a provider. The handler stack decides where secrets come from:

```python
# Production: Google Cloud Secret Manager
run(WithHandler(deploy_workflow(), gsm_handler), env={...})

# Local dev: 1Password
run(WithHandler(deploy_workflow(), onepassword_handler), env={...})

# CI: AWS Secrets Manager
run(WithHandler(deploy_workflow(), aws_secrets_handler), env={...})

# Fallback chain: try 1Password, fall back to env vars
run(WithHandler(WithHandler(
    deploy_workflow(),
    onepassword_handler),
    env_var_handler),
)
```

With DI, you'd need a `SecretService` protocol, provider implementations, a factory, and explicit wiring per environment. With effects, you change one line: the handler.

### 6. Testing Is Handler Swapping

```python
# Production
result = run(
    WithHandler(my_workflow(), openai_production_handler),
    env={"openai_api_key": real_key}
)

# Test — swap handler, everything else identical
result = run(
    WithHandler(my_workflow(), openai_mock_handler),
    store={"mock_config": MockOpenAIConfig(default_response="test")}
)

# Replay — record and replay real responses
result = run(
    WithHandler(my_workflow(), replay_handler(recorded_responses)),
)
```

DI can do this too (mock injection), but effects make it structural: the program itself never changes, only the handler stack.

## Where DI Is Better

Being honest about DI's strengths:

### 1. Simple Single-Provider Usage

If you only ever use OpenAI:

```python
# DI: clean, familiar
result = await llm.chat(messages, "gpt-4o")

# Effects: more ceremony for the same thing
result = yield LLMChat(messages=messages, model="gpt-4o")
```

Effects add value when you have multiple providers, cross-cutting concerns, or need observability. For a single provider with no extras, DI is simpler.

### 2. IDE Discoverability

```python
# DI: autocomplete shows all available methods
llm.  # -> chat(), structured(), embedding(), ...

# Effects: you need to know the effect class names
yield LLM  # -> LLMChat, LLMStructuredOutput, LLMEmbedding (via import autocomplete)
```

Protocol-based DI surfaces capabilities through method signatures. Effects require knowing the effect vocabulary.

### 3. Explicit Wiring

DI makes dependencies visible at construction time:

```python
# DI: clear what this service needs
service = MyService(
    llm=OpenAILLMService(api_key="..."),
    cache=RedisCache(url="..."),
    db=PostgresDB(dsn="..."),
)
```

With effects, dependencies are resolved at runtime by whatever handler is stacked. This is powerful but less explicit.

### 4. Static Analysis

Type checkers can fully verify DI wiring. Effects with `Delegate()` chains are harder to statically verify — you trust the handler stack at runtime.

## Decision Guide

| Scenario | Recommendation |
|----------|---------------|
| Single provider, simple usage | DI |
| Multi-provider, model-based routing | Effects |
| Need cross-cutting concerns (logging, cost, retry) across many services | Effects |
| Need live execution tracing | Effects |
| Rapid prototyping with familiar patterns | DI |
| Complex workflows composing LLM + Image + Secrets + Caching | Effects |
| Library code where users choose their provider | Effects |
| Already in the doeff ecosystem | Effects |
| Standalone microservice with fixed dependencies | DI |

## The Composability Argument

The deepest advantage of effects is **composability across concern boundaries**.

In DI, each service type needs its own wrapper hierarchy. When you add a new cross-cutting concern (say, audit logging), you touch every service wrapper. When you add a new service type, you implement every cross-cutting wrapper for it.

With effects, concerns are orthogonal:

```
                  Handlers (cross-cutting)
                  ┌────────┬─────────┬──────────┐
                  │ Retry  │ CostCap │ Tracing  │
    ┌─────────────┼────────┼─────────┼──────────┤
    │ LLMChat     │   ✓    │    ✓    │    ✓     │   doeff-llm
E   │ LLMEmbed    │   ✓    │    ✓    │    ✓     │   doeff-llm
f   │ ImageGen    │   ✓    │    ✓    │    ✓     │   doeff-image
f   │ GetSecret   │   ✓    │    ✗    │    ✓     │   doeff-secret
e   │ CacheGet    │   ✗    │    ✗    │    ✓     │   doeff (core)
c   └─────────────┴────────┴─────────┴──────────┘
t
s   Adding a new effect type:  1 row  (just the effect, handlers apply automatically)
    Adding a new handler:      1 column (just the handler, applies to all effects)
```

This maps to real packages:

| Effects Package | Provider Handlers |
|----------------|-------------------|
| `doeff-llm` | openai, gemini, openrouter, anthropic |
| `doeff-image` | seedream, gemini, dall-e |
| `doeff-secret` | google-secret-manager, 1password, aws, vault, env vars |

With DI, the same matrix requires N x M wrapper classes. This is the [Expression Problem](https://en.wikipedia.org/wiki/Expression_problem), and algebraic effects solve it elegantly.

## Case Study: Simulation Through Composition

The most striking example of effect composability is deterministic simulation. Consider a program that uses time, events, and concurrency:

```python
from doeff import do, Spawn, Wait
from doeff_time.effects import Delay, GetTime, ScheduleAt
from doeff_events.effects import Publish, WaitForEvent

@do
def trading_strategy():
    now = yield GetTime()

    # Spawn a background price monitor
    monitor = yield Spawn(price_monitor())

    # Schedule a market-open event at 9:30 AM
    yield ScheduleAt(market_open_time, Publish(MarketOpen()))

    # Wait for the event
    event = yield WaitForEvent(MarketOpen)

    # Delay before trading
    yield Delay(5.0)

    # Execute trade
    result = yield place_order(event.symbol, 100)
    return result
```

This program uses three independent effect domains:

| Effect | Package | Concern |
|--------|---------|---------|
| `Spawn`, `Wait` | doeff core | Concurrency |
| `Delay`, `GetTime`, `ScheduleAt` | doeff-time | Time |
| `Publish`, `WaitForEvent` | doeff-events | Pub/sub |

**None of these packages know about each other.** Yet they compose into a deterministic simulation:

```python
from doeff_sim.handlers import deterministic_sim_handler
from doeff_events.handlers import event_handler
from doeff_time.handlers import async_time_handler
from doeff import async_run, default_async_handlers

# Deterministic simulation (instant, reproducible)
result = run(
    WithHandler(
        WithHandler(
            trading_strategy(),
            event_handler(),              # Handles Publish/WaitForEvent
        ),
        deterministic_sim_handler(        # Handles Delay/GetTime/ScheduleAt
            start_time=1704067200.0,      #   + intercepts Spawn/Wait for
        ),                                #   deterministic scheduling
    ),
)

# Same program, real-time execution (wall-clock, asyncio)
result = await async_run(
    WithHandler(
        WithHandler(
            trading_strategy(),
            event_handler(),              # Same event handler
        ),
        async_time_handler(),             # asyncio.sleep, time.time()
    ),
    handlers=default_async_handlers(),
)
```

### How the Composition Works

The key insight is that **events know nothing about time, time knows nothing about events, and the simulation handler knows nothing about events** — yet time-ordered event dispatch emerges from composition:

```
yield ScheduleAt(t, Publish(event))
         │                  │
         │                  └─ event_handler: dispatches to listener queue via Promise
         └─ sim_handler: queues the Publish program at simulated time t
```

When `WaitForEvent` is yielded:
1. The **event handler** creates a `Promise`, registers it as a listener, and yields `Wait(promise.future)`
2. The **sim handler** intercepts `Wait` and parks the continuation in its scheduling queue
3. When sim time reaches `t`, the sim handler runs `Publish(event)`
4. The **event handler** handles `Publish`, completing the `Promise`
5. The **sim handler** sees the `Promise` resolved and wakes the parked continuation

Three independent handlers, zero knowledge of each other, composing through doeff core's `Promise`/`Wait` primitives.

### Why This Can't Work with DI

With DI, making this program work in both simulated and real time would require:

```python
class TimeService(Protocol):
    async def delay(self, seconds: float) -> None: ...
    async def get_time(self) -> float: ...
    async def schedule_at(self, time: float, callback: Callable) -> None: ...

class EventService(Protocol):
    async def publish(self, event: Any) -> None: ...
    async def wait_for_event(self, *types: type) -> Any: ...

class ConcurrencyService(Protocol):
    async def spawn(self, task: Callable) -> Task: ...
    async def wait(self, task: Task) -> Any: ...
```

Now you need three separate service implementations per mode (simulated, async, sync), but worse — the services need to **coordinate**. The simulated `TimeService` needs to control when `EventService` dispatches and when `ConcurrencyService` resumes tasks. You end up with:

- A `SimulatedTimeService` that holds a reference to `SimulatedEventService` and `SimulatedConcurrencyService`
- A shared priority queue that all three services access
- Tight coupling between implementations that are supposed to be independent

With DI, you'd inevitably build a monolithic `SimulationEngine` that bundles time, events, and concurrency together — exactly the 4,589-line interpreter we're decomposing. The independence is an illusion.

With effects, the independence is real. Each handler manages its own concern. The `deterministic_sim_handler` only intercepts time and concurrency effects. The `event_handler` only handles pub/sub. They compose through the program's yield points and doeff's handler stack — no shared state, no circular references, no coordination protocol.

### The Composability Matrix (Extended)

```
                  Handlers (cross-cutting)
                  ┌────────┬─────────┬──────────┬─────────┬───────────┐
                  │ Retry  │ CostCap │ Tracing  │ SimTime │ EventBus  │
    ┌─────────────┼────────┼─────────┼──────────┼─────────┼───────────┤
    │ LLMChat     │   ✓    │    ✓    │    ✓     │    ·    │     ·     │
    │ ImageGen    │   ✓    │    ✓    │    ✓     │    ·    │     ·     │
E   │ GetSecret   │   ✓    │    ·    │    ✓     │    ·    │     ·     │
f   │ Delay       │   ·    │    ·    │    ✓     │    ✓    │     ·     │
f   │ ScheduleAt  │   ·    │    ·    │    ✓     │    ✓    │     ·     │
e   │ Spawn/Wait  │   ·    │    ·    │    ✓     │    ✓    │     ·     │
c   │ Publish     │   ·    │    ·    │    ✓     │    ·    │     ✓     │
t   │ WaitForEvt  │   ·    │    ·    │    ✓     │    ·    │     ✓     │
s   └─────────────┴────────┴─────────┴──────────┴─────────┴───────────┘

    ✓ = handler intercepts     · = handler delegates (transparent)
```

Every `·` in this matrix is a handler calling `Delegate()` — the effect passes through transparently. No wrapper needed. No code written. The handler simply doesn't care about effects outside its domain.

## Summary

Effects aren't universally better than DI. They're better when:

1. **Multiple providers** serve the same effect type (LLM, image, etc.)
2. **Cross-cutting concerns** need to apply uniformly across many service types
3. **Runtime observability** of the full execution trace matters
4. **Handler composition** is the natural way to express your system's configuration
5. **Orthogonal concerns** (time, events, concurrency, I/O) need to compose without coordination

If you're building a multi-provider AI system with cost tracking, retry logic, and live tracing — effects are the right tool. If you're writing a deterministic simulation where time, events, and concurrency compose independently — effects are the *only* tool that keeps them truly independent. If you're calling one API with one client — just use DI.
