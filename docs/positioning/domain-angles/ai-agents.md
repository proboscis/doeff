# Domain Angle: AI Agent Development

The highest-urgency opportunity. Everyone is building agents. The pain is extreme. The timing is now.

## The Pain

Every AI agent developer faces these problems daily:

1. **Every debug cycle costs money** — re-running an agent loop = re-calling the LLM API
2. **Reproducing bugs is expensive** — "it hallucinated once" requires re-running the entire session
3. **Testing requires real APIs or complex mocking** — no clean way to test agent logic in isolation
4. **Swapping models means rewriting** — OpenAI -> Claude -> local requires integration changes
5. **No visibility** — why did the agent choose that tool? Why did it loop 7 times?

## The doeff Approach: LLM Call = Effect

```python
@do
def research_agent(query: str) -> Program[Report]:
    plan = yield LLMChat(
        messages=[{"role": "user", "content": f"Plan research steps for: {query}"}],
        model="gpt-4o",
    )
    sources = yield Gather([
        web_search(step) for step in plan.steps
    ])
    draft = yield LLMChat(
        messages=[{"role": "user", "content": f"Synthesize: {sources}"}],
        model="claude-sonnet",
    )
    review = yield LLMChat(
        messages=[{"role": "user", "content": f"Review: {draft}"}],
        model="gpt-4o",
    )
    return review
```

Every LLM call, tool use, and decision point is a yield. The program is a pure description of what to do. The handlers decide how.

## The Three Capabilities No Other Framework Has

### 1. Replay Agent Sessions at $0

```python
# Record a real agent run
result = run(research_agent("quantum computing"), handlers=[
    RecordingHandler("sessions/run_42.json"),
    OpenAIHandler(api_key="..."),
    ClaudeHandler(api_key="..."),
    WebSearchHandler(),
])

# Replay to debug — zero API cost
result = run(research_agent("quantum computing"), handlers=[
    ReplayHandler("sessions/run_42.json"),
])
# Instant. Deterministic. Free.
```

A user reports "the agent gave wrong results for X." Replay their session. Step through every effect. Find where it went wrong. No API calls needed.

### 2. Swap Models Without Code Changes

```python
# Production: GPT-4o + Claude
result = run(research_agent(query), handlers=[
    OpenAIHandler(model="gpt-4o"),
    ClaudeHandler(model="claude-sonnet"),
])

# Testing: local models
result = run(research_agent(query), handlers=[
    OllamaHandler(model="llama-3-70b"),
])

# Evaluation: compare models
for model in ["gpt-4o", "claude-sonnet", "gemini-2-pro"]:
    result = run(research_agent(query), handlers=[
        UnifiedLLMHandler(model=model),
    ])
```

The program never mentions a provider. The handler stack decides.

### 3. Automatic Cost Tracking

Because every LLM call is an effect, you get cost tracking for free:

```python
from doeff import Delegate, Effect, Get, do

# Cost-capping handler — applies to ALL LLM effects
@do
def cost_cap_handler(effect: Effect, k):
    if isinstance(effect, LLMChat):
        current = yield Get("total_cost")
        if current > MAX_BUDGET:
            raise BudgetExceededError(f"${current:.2f} > ${MAX_BUDGET}")
    yield Delegate()

result = run(
    WithHandler(research_agent(query), cost_cap_handler),
    handlers=[OpenAIHandler(), ClaudeHandler()],
)
print(result.effect_log)
# LLMChat: 3 calls, $0.12 total, 4.2s latency
# WebSearch: 5 calls, 2.1s latency
```

## Comparison with Agent Frameworks

| Feature | LangChain | Pydantic AI | CrewAI | doeff |
|---------|-----------|-------------|--------|-------|
| LLM call abstraction | Runnable chain | Agent + model | Agent internals | **yield LLMChat(...)** |
| Model swapping | Reconfigure chain | Agent.override() | Agent config | **Handler swap** |
| Testing | FakeListLLM | TestModel (good) | @patch (bad) | **Handler swap** |
| Replay | LangSmith traces (view only) | capture_run_messages | crewai replay (task level) | **Effect-level recording** |
| Cost tracking | LangSmith (SaaS) | Logfire (SaaS) | None | **Effect-level (built in)** |
| Cross-cutting concerns | Callbacks | None | None | **Handler composition** |

## The Academic Backing

Wang 2025 ([arXiv:2507.22048](https://arxiv.org/abs/2507.22048)) formally proved that treating LLM calls as algebraic effects enables 10x speedups via composable handler-based parallelization. This is exactly what doeff implements.

Effect-TS already ships `@effect/ai` packages that model LLM calls as typed effects, used in production by 14.ai for customer support agents.

## The Pitch

> "LangChain wraps your LLM calls in 10 layers of abstraction. Pydantic AI gives you typed DI. doeff gives you something neither can: record any agent session and replay it at zero cost. Because every LLM call is an effect, and effects can be intercepted, recorded, and replayed."

## The Demo (Under 5 Minutes)

1. Build a simple research agent (10 lines of doeff)
2. Run it against real APIs — show the effect trace
3. Record the session to a JSON file
4. Replay it — show identical output, zero API calls
5. Swap GPT-4o for Claude — show one-line change
6. Add a cost cap handler — show it applies to all LLM calls

Total: ~50 lines of code. All three stages of the pitch in one demo.
