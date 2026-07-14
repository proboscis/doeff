# doeff Documentation

Welcome to the comprehensive documentation for doeff - an algebraic effects system with one-shot continuations for Python, backed by a Rust VM.

## Quick Links

- **[GitHub Repository](https://github.com/proboscis/doeff)**
- **[PyPI Package](https://pypi.org/project/doeff/)**
- **[Issue Tracker](https://github.com/proboscis/doeff/issues)**

## Table of Contents

### Getting Started

1. **[Getting Started](01-getting-started.md)** - Installation, first program, basic concepts
2. **[Core Concepts](02-core-concepts.md)** - Program, Effect, execution model, type system

### Effect Types

3. **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer effects
4. **[Async Effects](04-async-effects.md)** - Gather, Spawn, Await for async operations
5. **[Error Handling](05-error-handling.md)** - Result, Try for error handling
6. **[Cache System](07-cache-system.md)** - Cache effects with policies and handlers
7. **[Advanced Effects](09-advanced-effects.md)** - Gather, concurrency patterns
8. **[Semaphore Effects](21-semaphore-effects.md)** - Create, acquire, and release permits with FIFO fairness

### Integration & Advanced Topics

11. **[Kleisli Arrows](11-kleisli-arrows.md)** - Composition and automatic unwrapping
12. **[Patterns](12-patterns.md)** - Best practices and common patterns
13. **[API Reference](13-api-reference.md)** - Complete API documentation

### CLI Tools

11. **[CLI Auto-Discovery](14-cli-auto-discovery.md)** - Automatic interpreter and environment discovery
12. **[CLI Script Execution](15-cli-script-execution.md)** - Execute Python scripts with program execution results
13. **[Effect Boundaries](17-effect-boundaries.md)** - Effect vs escape architecture and runner boundaries
14. **[Effect Combinations](18-effect-combinations.md)** - Composition laws and interaction guarantees across effects
15. **[Agent Tutorial](19-agent-tutorial.md)** - Building an automated code review system

### Specialized Topics

- **[MARKERS.md](MARKERS.md)** - Marker-based Program manipulation
- **[seedream.md](seedream.md)** - SeeDream integration
- **[IDE Plugins](ide-plugins.md)** - PyCharm and VS Code extensions
- **[Program Architecture](program-architecture-overview.md)** - Runtime internals overview
- **[Removed IO API](06-io-effects.md)** - Historical note for the removed `IO(...)` effect
- **[Removed run_program API](16-run-program-api.md)** - Historical note for the removed Python `run_program()` entrypoint

### Gemini Integration

- **[Gemini Setup](gemini_client_setup.md)** - API key and ADC configuration
- **[Gemini Cost Hook](gemini_cost_hook.md)** - Custom cost calculation
- **[Unified LLM Effects](llm_unified_effects.md)** - Provider-agnostic LLM effects and handler stacking

### Design Notes

- **[Why Effects Over DI?](20-why-effects-over-di.md)** - Real-world use cases where algebraic effects beat dependency injection
- **[Capability Classes](22-capability-classes.md)** - A four-class taxonomy predicting where effects pay off, retry under one-shot continuations, and adoption anti-patterns
- **[CLI Architecture](cli-run-command-architecture.md)** - Run command pipeline design
- **[Filesystem Effects](filesystem-effect-architecture.md)** - Filesystem effect design (draft)
- **[Abstraction Concern](abstraction_concern.md)** - Interpreter design discussion

## What is doeff?

doeff is an **algebraic effects** system for Python. Effects are first-class operations that can be intercepted and handled by composable handlers. The runtime uses **one-shot continuations** — each effect invocation suspends, gets handled, and resumes exactly once — backed by a high-performance **Rust VM**.

Key characteristics:

- **Algebraic effects with handlers**: Define effects as data, handle them with composable, swappable handlers
- **One-shot continuations**: Each continuation resumes exactly once (unlike multi-shot systems like Koka or Eff)
- **Rust VM runtime**: High-performance effect handling and continuation management
- **Batteries-included handlers**: Reader, State, Writer, Scheduler, Result — ready to use
- **Generator-based do-notation**: Write effectful code that looks like regular Python
- **Stack-safe execution**: Trampolining prevents stack overflow
- **Type safety**: Full type annotations with `.pyi` files
- **Pinjected integration**: Bridge to dependency injection framework

## Quick Example

```python
from doeff import do, run
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

@do
def example_workflow():
    # State management
    yield Put("counter", 0)

    # Logging
    yield Tell("Starting computation")

    # State updates
    yield Put("result", 42)
    count = yield Get("result")

    return count

prog = example_workflow()
prog = writer(prog)
prog = state()(prog)
result = run(scheduled(prog))
print(f"Result: {result}")  # Result: 42
```

## Learning Path

### For Beginners

1. Start with **[Getting Started](01-getting-started.md)** for installation and basics
2. Read **[Core Concepts](02-core-concepts.md)** to understand algebraic effects, handlers, and the execution model
3. Learn **[Basic Effects](03-basic-effects.md)** for Reader, State, Writer
4. Explore **[Error Handling](05-error-handling.md)** for robust programs

### For Intermediate Users

1. **[Async Effects](04-async-effects.md)** for concurrent operations
2. **[Cache System](07-cache-system.md)** for performance optimization
3. **[Kleisli Arrows](11-kleisli-arrows.md)** for elegant composition
4. **[Patterns](12-patterns.md)** for best practices

### For Advanced Users

1. **[Advanced Effects](09-advanced-effects.md)** for Gather, concurrency
2. **[Semaphore Effects](21-semaphore-effects.md)** for concurrency control
3. **[Effect Boundaries](17-effect-boundaries.md)** for architecture design
4. **[API Reference](13-api-reference.md)** for complete API details

## By Use Case

### Building Web Applications

- **[Async Effects](04-async-effects.md)** for HTTP requests
- **[Cache System](07-cache-system.md)** for response caching
- **[Error Handling](05-error-handling.md)** for request validation
- **[Patterns](12-patterns.md#web-application-patterns)** for common patterns

### Data Processing Pipelines

- **[Basic Effects](03-basic-effects.md)** for state management
- **[Async Effects](04-async-effects.md)** for parallel processing
- **[Error Handling](05-error-handling.md)** for retry logic
- **[Patterns](12-patterns.md#pipeline-patterns)** for data flow

### CLI Applications

- **[Basic Effects](03-basic-effects.md)** for configuration and local state
- **[Async Effects](04-async-effects.md)** for external work and concurrency
- **[Error Handling](05-error-handling.md)** for validation
- **[Effect Boundaries](17-effect-boundaries.md)** for observability and auditing

### Testing

- **[Patterns](12-patterns.md#testing-patterns)** for testing strategies
- **[Error Handling](05-error-handling.md)** for error simulation

## Effect Quick Reference

| Category | Effects | Source |
|----------|---------|--------|
| **Reader** | `Ask`, `Local` | `doeff_core_effects` |
| **State** | `Get`, `Put` | `doeff_core_effects` |
| **Writer** | `Tell`, `slog`, `Listen` | `doeff_core_effects` |
| **Error** | `Try` | `doeff_core_effects` |
| **Scheduler** | `Spawn`, `Wait`, `Gather`, `Race`, `Cancel` | `doeff_core_effects.scheduler` |
| **Promise** | `CreatePromise`, `CompletePromise`, `FailPromise` | `doeff_core_effects.scheduler` |
| **Semaphore** | `CreateSemaphore`, `AcquireSemaphore`, `ReleaseSemaphore` | `doeff_core_effects.scheduler` |
| **Cache** | `MemoGet`, `CachePut` | `doeff_core_effects.cache_effects` |

## Common Patterns

### Configuration + State + Logging

```python
@do
def application():
    config = yield Ask("config")
    yield Put("status", "running")
    yield Tell(f"Started with config: {config}")
    result = yield do_work()
    return result
```

### Concurrency + Error Handling

```python
@do
def robust_workflow():
    result = yield Try(risky_operation())
    if isinstance(result, Ok):
        return result.value
    else:
        yield Tell(f"Failed: {result.error}")
        return None
```

### Parallel Processing + Aggregation

```python
@do
def process_batch(items):
    spawned = []
    for item in items:
        task = yield Spawn(process_item(item))
        spawned.append(task)
    results = yield Gather(*spawned)
    return {"processed": len(results), "results": results}
```

## Contributing

See the [GitHub repository](https://github.com/proboscis/doeff) for:
- Filing issues
- Submitting pull requests
- Development guidelines

## License

MIT License - see [LICENSE](https://github.com/proboscis/doeff/blob/main/LICENSE) for details.

## Author

Kento Masui (nameissoap@gmail.com)

This project evolved from earlier internal prototypes.
