# Python AI/LLM Framework Landscape (Feb 2026)

## The State of Play

The Python AI orchestration landscape is fragmented and in flux. LangChain (126k stars) dominates mindshare but is losing production users. The community is migrating toward simpler, more principled alternatives. No framework has claimed the "effects-based orchestration" position.

## Framework Comparison

### Quick Matrix

| Framework | Stars | Core Paradigm | Testing | Replay | Effect Separation |
|-----------|-------|---------------|---------|--------|-------------------|
| **LangChain** | 126.5k | Chains/LCEL/Runnables | FakeListLLM (manual) | LangSmith traces (observe only) | Poor — callbacks |
| **Pydantic AI** | 14.8k | Agent + typed deps | TestModel + override (excellent) | capture_run_messages | Clean — DI |
| **DSPy** | 32.2k | Signatures + optimizers | DummyLM (3 modes) | inspect_history() | Clean — configure() |
| **Haystack** | 24.2k | Pipeline DAG | Component isolation | Breakpoints + snapshots + resume | Clean — component protocol |
| **CrewAI** | 44.0k | Role-based agent teams | @patch + mock | crewai replay CLI | Poor — agent internals |
| **AutoGen** | 54.5k | Multi-agent conversations | ReplayChatCompletionClient | Event-driven traces | Partial — message protocol |
| **Instructor** | 12.4k | Structured output extraction | Standard pytest + mock | N/A (stateless) | Clean — thin wrapper |
| **Marvin** | 6.1k | Ambient AI functions | assert_llm_equal (needs LLM) | N/A | Partial — decorator magic |
| **Semantic Kernel** | 27.2k | Plugins + planners | Standard mocking | OpenTelemetry | Poor — kernel object |
| **smolagents** | 25.4k | Code-first agents | Standard pytest | N/A | Partial — tool decorator |
| **doeff** | - | Algebraic effects + handlers | Handler swap (theoretically best) | Effect recording + replay | Native — effects ARE the separation |

### Architectural Families

```
1. PIPELINE / DAG
   Haystack, LlamaIndex
   Components -> Pipeline -> Execute
   Best for: Structured data flows, RAG
   Testing: Components are independently testable

2. GRAPH / STATE MACHINE
   LangGraph, Pydantic Graph (beta)
   Nodes + Edges + State -> Checkpointed execution
   Best for: Complex agent logic with explicit control flow

3. MULTI-AGENT CONVERSATION
   AutoGen, CrewAI, smolagents
   Agents + Roles + Messages -> Emergent behavior
   Best for: Collaborative reasoning
   Testing: Non-deterministic; hard to isolate

4. DECLARATIVE / FUNCTIONAL
   DSPy, Instructor, Pydantic AI
   Signatures/Schemas + Optimization -> Structured output
   Best for: Reliable, testable LLM interactions

5. ALGEBRAIC EFFECTS (doeff)
   Program + Effect + Handler -> Composable execution
   Best for: Everything above, unified under one paradigm
   Testing: Handler swap — the only framework where testing is architectural, not bolted on
```

## The Testing Tier List

```
TIER 1 (Excellent):
  Pydantic AI .... TestModel + FunctionModel + Agent.override + ALLOW_MODEL_REQUESTS
  DSPy .......... DummyLM (3 modes) + dspy.configure() + Evaluate framework
  Haystack ...... Component protocol + independent run() + breakpoints

TIER 2 (Adequate):
  LangChain ..... FakeListLLM family (many variants, but manual wiring)
  AutoGen ....... ReplayChatCompletionClient
  Instructor .... Stateless design = standard mocking works

TIER 3 (Difficult):
  CrewAI ........ unittest.mock.patch on internals
  Semantic Kernel Standard mocking, no test utilities
  LlamaIndex .... No built-in mocks
  smolagents .... No built-in mocks
  Marvin ........ Tests require real LLM calls
```

doeff's testing story is architecturally superior to all of these — handler swap is the testing mechanism, not a bolt-on. But this needs to be demonstrated, not claimed.

## The Replay Tier List

```
FULL REPLAY:
  Haystack ...... Breakpoints -> JSON snapshots -> resume from any point

PARTIAL REPLAY:
  CrewAI ........ crewai replay CLI (task boundaries only)
  LangGraph ..... Checkpointed state (graph nodes)
  AutoGen ....... save_state_json() / load_state_json()
  Pydantic AI ... capture_run_messages + TestModel (deterministic re-execution)
  DSPy .......... inspect_history() + DummyLM

TRACE-BASED (observe, not replay):
  LangSmith ..... Full traces + langsmith-fetch CLI
  LlamaIndex .... LlamaTrace / OpenTelemetry
  Semantic Kernel OpenTelemetry

NO REPLAY:
  Instructor, Marvin, smolagents
```

doeff can do full effect-level replay — record all effect invocations, replay by providing a handler that returns recorded results. This is more granular than Haystack's component-level snapshots.

## Threat Assessment

### Pydantic AI (HIGH THREAT)

The closest competitor to doeff's value proposition. Already has:
- `TestModel` / `FunctionModel` for zero-cost testing
- `Agent.override()` for handler-like swapping
- `RunContext[T]` for typed dependency injection (approximates Reader effect)
- `ALLOW_MODEL_REQUESTS = False` as a safety net
- Growing fast (14.8k stars, Pydantic brand)

**What Pydantic AI lacks that doeff has:**
- Cross-cutting handlers (N effects x M handlers vs N x M wrappers)
- Effect interception without wrapping
- Composable handler stacks (routing, retry, cost cap — all orthogonal)
- Replay via effect recording
- Deterministic simulation via handler composition

**Risk:** Prefect is building [durable execution for Pydantic AI](https://www.prefect.io/blog/prefect-pydantic-integration). If they add recording/replay, the gap narrows significantly.

### DSPy (MEDIUM THREAT, COMPLEMENTARY)

DSPy optimizes *prompts* (what to say to the LLM). doeff optimizes *orchestration* (how to execute the conversation). They're complementary:
- DSPy modules could be wrapped as doeff effects
- doeff handlers could orchestrate DSPy-optimized prompts
- The combination would be uniquely powerful

### Haystack (MEDIUM THREAT)

Only framework with true breakpoint/snapshot replay. Their component protocol is clean. But:
- DAG-only (no algebraic composition)
- No cross-cutting handlers
- Enterprise-focused (deepset commercial platform)

### Effect-TS (WATCH)

Proves the "LLM call as effect" pattern works in production (14.ai uses it). TypeScript, not Python, but validates the approach. Key packages: `@effect/ai`, `@effect/ai-openai`, `@effect/ai-anthropic`.

## The Convergence

Four independent efforts are arriving at the same conclusion:

1. **Academic** — Wang 2025: LLM calls as algebraic effects (10x speedup)
2. **Academic** — Zhang/Yao MCE: Monadic agent workflows
3. **Production** — Effect-TS: Typed effects for AI in TypeScript
4. **Pragmatic** — Pydantic AI: Approximating effects via DI patterns

doeff is the principled Python implementation of what all four are converging toward.

## Sources

- LangChain critique: [minimaxir](https://minimaxir.com/2023/07/langchain-problem/), [Octomind](https://www.octomind.dev/blog/why-we-no-longer-use-langchain-for-building-our-ai-agents), [Stackademic](https://blog.stackademic.com/langchain-made-our-ai-app-slow-we-rewrote-without-it-6386b78880d3)
- Framework comparisons: [LangWatch](https://langwatch.ai/blog/best-ai-agent-frameworks-in-2025-comparing-langgraph-dspy-crewai-agno-and-more), [Vellum](https://www.vellum.ai/blog/top-langchain-alternatives)
- Pydantic AI testing: [ai.pydantic.dev/testing](https://ai.pydantic.dev/testing/)
- Haystack breakpoints: [deepset-ai/haystack](https://github.com/deepset-ai/haystack/blob/main/haystack/dataclasses/breakpoints.py)
- Effect-TS AI: [effect.website/blog/effect-ai](https://effect.website/blog/effect-ai/)
