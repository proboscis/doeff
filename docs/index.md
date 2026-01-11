# doeff Documentation

Welcome to the comprehensive documentation for doeff - a pragmatic free monad implementation for Python.

## Quick Links

- **[GitHub Repository](https://github.com/CyberAgentAILab/doeff)**
- **[PyPI Package](https://pypi.org/project/doeff/)**
- **[Issue Tracker](https://github.com/CyberAgentAILab/doeff/issues)**

## Table of Contents

### Getting Started

1. **[Getting Started](01-getting-started.md)** - Installation, first program, basic concepts
2. **[Core Concepts](02-core-concepts.md)** - Program, Effect, execution model, type system

### Effect Types

3. **[Basic Effects](03-basic-effects.md)** - Reader, State, Writer effects
4. **[Async Effects](04-async-effects.md)** - Future, Await, Parallel for async operations
5. **[Error Handling](05-error-handling.md)** - Result, Fail, Catch, Retry, Safe
6. **[IO Effects](06-io-effects.md)** - IO and Print for side effects
7. **[Cache System](07-cache-system.md)** - Cache effects with policies and handlers
8. **[Graph Tracking](08-graph-tracking.md)** - Execution tracking and visualization
9. **[Advanced Effects](09-advanced-effects.md)** - Gather, Memo, Atomic operations

### Integration & Advanced Topics

10. **[Pinjected Integration](10-pinjected-integration.md)** - Dependency injection with pinjected
11. **[Kleisli Arrows](11-kleisli-arrows.md)** - Composition and automatic unwrapping
12. **[Patterns](12-patterns.md)** - Best practices and common patterns
13. **[API Reference](13-api-reference.md)** - Complete API documentation

### Runtime & Scheduling

20. **[Runtime Scheduler](20-runtime-scheduler.md)** - Single-shot continuations, pluggable schedulers, and simulation effects

### CLI Tools

14. **[CLI Auto-Discovery](14-cli-auto-discovery.md)** - Automatic interpreter and environment discovery
15. **[CLI Script Execution](15-cli-script-execution.md)** - Execute Python scripts with program execution results
16. **[Python run_program API](16-run-program-api.md)** - Use CLI-equivalent discovery from Python tests or scripts
17. **[Workflow Observability](17-workflow-observability.md)** - Live effect tracing with doeff-flow

### Agent Session Management

18. **[Agent Session Management](18-agent-session-management.md)** - Managing coding agents (Claude, Codex, Gemini) in tmux
19. **[Agent Tutorial](19-agent-tutorial.md)** - Building an automated code review system

### Specialized Topics

- **[MARKERS.md](MARKERS.md)** - Marker-based Program manipulation
- **[cache.md](cache.md)** - Detailed cache system documentation
- **[seedream.md](seedream.md)** - SeeDream integration
- **[IDE Plugins](ide-plugins.md)** - PyCharm and VS Code extensions
- **[Program Architecture](program-architecture-overview.md)** - Runtime internals overview

### Gemini Integration

- **[Gemini Setup](gemini_client_setup.md)** - API key and ADC configuration
- **[Gemini Cost Hook](gemini_cost_hook.md)** - Custom cost calculation

### Design Notes

- **[CLI Architecture](cli-run-command-architecture.md)** - Run command pipeline design
- **[Filesystem Effects](filesystem-effect-architecture.md)** - Filesystem effect design (draft)
- **[Abstraction Concern](abstraction_concern.md)** - Interpreter design discussion

## What is doeff?

doeff is a pragmatic effects system for Python that provides:

- **Generator-based do-notation**: Write monadic code that looks like regular Python
- **Comprehensive effects**: Reader, State, Writer, Future, Result, IO, Cache, Graph tracking
- **Stack-safe execution**: Trampolining prevents stack overflow
- **Type safety**: Full type annotations with `.pyi` files
- **Pinjected integration**: Bridge to dependency injection framework

## Quick Example

```python
from doeff import do, Put, Get, Log, Await, ProgramInterpreter
import asyncio

@do
def example_workflow():
    # State management
    yield Put("counter", 0)
    
    # Logging
    yield Log("Starting computation")
    
    # Async operations
    data = yield Await(fetch_data())
    
    # State updates
    yield Put("result", len(data))
    count = yield Get("result")
    
    return count

async def main():
    interpreter = ProgramInterpreter()
    result = await interpreter.run(example_workflow())
    print(f"Result: {result.value}")

asyncio.run(main())
```

## Learning Path

### For Beginners

1. Start with **[Getting Started](01-getting-started.md)** for installation and basics
2. Read **[Core Concepts](02-core-concepts.md)** to understand Program and Effect
3. Learn **[Basic Effects](03-basic-effects.md)** for Reader, State, Writer
4. Explore **[Error Handling](05-error-handling.md)** for robust programs

### For Intermediate Users

1. **[Async Effects](04-async-effects.md)** for concurrent operations
2. **[Cache System](07-cache-system.md)** for performance optimization
3. **[Kleisli Arrows](11-kleisli-arrows.md)** for elegant composition
4. **[Patterns](12-patterns.md)** for best practices

### For Advanced Users

1. **[Advanced Effects](09-advanced-effects.md)** for Gather, Memo, Atomic
2. **[Graph Tracking](08-graph-tracking.md)** for execution visualization
3. **[Pinjected Integration](10-pinjected-integration.md)** for DI patterns
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

- **[IO Effects](06-io-effects.md)** for user interaction
- **[Error Handling](05-error-handling.md)** for validation
- **[Basic Effects](03-basic-effects.md)** for configuration

### Testing

- **[Patterns](12-patterns.md#testing-patterns)** for testing strategies
- **[Pinjected Integration](10-pinjected-integration.md)** for dependency injection
- **[Error Handling](05-error-handling.md)** for error simulation

## Effect Quick Reference

| Category | Effects | Chapter |
|----------|---------|---------|
| **Reader** | Ask, Local | [03](03-basic-effects.md#reader-effects) |
| **State** | Get, Put, Modify, AtomicGet, AtomicUpdate | [03](03-basic-effects.md#state-effects), [09](09-advanced-effects.md#atomic-effects) |
| **Writer** | Log, Tell, Listen, StructuredLog | [03](03-basic-effects.md#writer-effects) |
| **Future** | Await, Parallel | [04](04-async-effects.md) |
| **Result** | Fail, Catch, Retry, Recover, Safe, Finally, FirstSuccess | [05](05-error-handling.md) |
| **IO** | IO, Print | [06](06-io-effects.md) |
| **Cache** | CacheGet, CachePut | [07](07-cache-system.md) |
| **Graph** | Step, Annotate, Snapshot, CaptureGraph | [08](08-graph-tracking.md) |
| **Gather** | Gather | [09](09-advanced-effects.md#gather-effects) |
| **Memo** | MemoGet, MemoPut | [09](09-advanced-effects.md#memo-effects) |
| **Dep** | Dep | [10](10-pinjected-integration.md) |

## Common Patterns

### Configuration + State + Logging

```python
@do
def application():
    config = yield Ask("config")
    yield Put("status", "running")
    yield Log(f"Started with config: {config}")
    result = yield do_work()
    return result
```

### Async + Error Handling + Retry

```python
@do
def robust_fetch(url):
    result = yield Retry(
        Await(httpx.get(url)),
        max_attempts=3,
        delay_ms=1000
    )
    return result
```

### Parallel Processing + Aggregation

```python
@do
def process_batch(items):
    tasks = [process_item(item) for item in items]
    results = yield Parallel(*tasks)
    return {"processed": len(results), "results": results}
```

## Contributing

See the [GitHub repository](https://github.com/CyberAgentAILab/doeff) for:
- Filing issues
- Submitting pull requests
- Development guidelines

## License

MIT License - see [LICENSE](https://github.com/CyberAgentAILab/doeff/blob/main/LICENSE) for details.

## Author

Kento Masui (nameissoap@gmail.com)

Originally extracted from the `sge-hub` project's `pragmo` module.
