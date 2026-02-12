# doeff Positioning

Internal documents for positioning doeff in the Python ecosystem. These are selling arguments, competitive analysis, and domain-specific pitches — not user-facing docs.

## The Core Narrative

Wang 2025 proved LLM calls are algebraic effects. Effect-TS shipped it in TypeScript. Pydantic AI is approximating it without the theory. doeff is the principled Python implementation — backed by a Rust VM, with batteries-included handlers, and a paradigm that the research community is independently converging toward.

## The Three-Stage Pitch

```
1. Write it flat          (2x cleaner)     <- gets attention
2. Test without mocks     (5x cleaner)     <- gets interest
3. Replay from recordings (impossible before) <- gets adoption
```

The sequentiality is the hook, not the sale. The sale is what it enables: because all effects are yields, you can intercept, record, replay, and swap all of them.

## The Fundamental Argument

Algebraic effects are a superset of async/await. Python adopted async/await because callback hell was unreadable. doeff generalizes that same transformation to ALL side effects — not just async IO.

But "superset" alone doesn't sell. What sells is the specific pain point that's sharp enough to drive adoption. The sharpest candidates:

1. **AI agent debugging** — replay agent sessions at $0 cost
2. **Mock-free testing** — swap handlers instead of patching
3. **Cross-cutting composition** — N effects x M handlers, not N x M wrapper classes

## Documents

### Competitive Analysis

- **[Framework Landscape (2026)](landscape-2026.md)** — Comparison of 11 frameworks: LangChain, Pydantic AI, DSPy, Haystack, CrewAI, AutoGen, Instructor, Marvin, Semantic Kernel, smolagents
- **[LangChain Critique](langchain-critique.md)** — Why LangChain fails, mapped to PL theory, and how effects solve each problem
- **[Academic Validation](academic-validation.md)** — Published research backing the "LLM call = algebraic effect" thesis

### Pitch Materials

- **[Three-Stage Pitch](three-stage-pitch.md)** — The compound pitch: flat yields -> zero-mock testing -> replay

### Domain-Specific Angles

- **[ML Pipelines](domain-angles/ml-pipelines.md)** — archpainter rewrite story, experiment replay
- **[AI Agents](domain-angles/ai-agents.md)** — LLM call as effect, agent session replay, model swapping
- **[Batch/ETL Jobs](domain-angles/batch-etl.md)** — Retry, checkpointing, partial failure recovery
- **[FastAPI/Web](domain-angles/fastapi.md)** — Depends() -> Ask effect generalization
- **[Gradio/Prototyping](domain-angles/gradio-prototyping.md)** — Interactive step-through, fork-and-explore, effect trace as UI (NOT DI)
- **[Card Game Simulation](domain-angles/card-game-simulation.md)** — Same game logic: interactive UI, 10k AI simulations, LLM player, replay viewer
- **[Trading Backtesting](domain-angles/trading-backtesting.md)** — Same strategy: backtest (simulated time), paper trade, live trade, replay

## Key Competing Frameworks to Track

| Framework | Stars | Threat Level | Why |
|-----------|-------|-------------|-----|
| **Pydantic AI** | 14.8k | HIGH | Already 80% of doeff's testing story without the theory |
| **DSPy** | 32.2k | MEDIUM | Complementary (optimizes prompts, not orchestration) |
| **Haystack** | 24.2k | MEDIUM | Has breakpoint/snapshot replay — closest to effect replay |
| **Effect-TS** | - | WATCH | Proves effects work for AI in production (TypeScript) |
| **LangChain** | 126.5k | LOW | Community is leaving; validates the problem doeff solves |

## Key Academic References

| Paper | Date | Key Finding |
|-------|------|-------------|
| Wang 2025 (arXiv:2507.22048) | Jul 2025 | LLM calls as algebraic effects, 10x speedup via composable handlers |
| Zhang et al. MCE (arXiv:2512.22431) | Dec 2025 | Monadic agent workflows, co-authored with Turing Award winner Andrew Yao |
| Laurent & Platzer, Oracular Programming (arXiv:2502.05310) | Feb 2025 | LLM calls as nondeterministic choice in search trees |

## The Window

Pydantic AI + Prefect are building durable execution (replay-like) for agents. If they ship a recording/replay layer, doeff's unique 20% shrinks. The window to claim the "principled effects for Python AI" position is now.
