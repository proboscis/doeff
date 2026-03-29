"""
Core handlers — reader, state, writer.

Each is a function that takes config and returns a @do handler function.
Use with WithHandler:

    run(WithHandler(reader(env={"key": "value"}),
        WithHandler(state(initial={"count": 0}),
            body())))
"""

from doeff import do
from doeff.program import Resume, Pass

from doeff_core_effects.effects import (
    Ask, Get, Put, Tell, Try, Slog, WriterTellEffect,
    Local, Listen, Await,
)


def reader(env=None):
    """Reader handler: resolves Ask(key) from env dict.

    Args:
        env: dict mapping keys to values. Default empty.
    """
    if env is None:
        env = {}

    @do
    def handler(effect, k):
        if isinstance(effect, Ask):
            if effect.key in env:
                result = yield Resume(k, env[effect.key])
                return result
            from doeff.program import ResumeThrow
            return (yield ResumeThrow(k, KeyError(_missing_key_message(effect.key))))
        yield Pass(effect, k)

    return handler


def state(initial=None):
    """State handler: resolves Get(key) and Put(key, value) from mutable dict.

    Args:
        initial: dict of initial state. Default empty.
    """
    store = dict(initial) if initial else {}

    @do
    def handler(effect, k):
        if isinstance(effect, Get):
            result = yield Resume(k, store.get(effect.key))
            return result
        elif isinstance(effect, Put):
            store[effect.key] = effect.value
            result = yield Resume(k, None)
            return result
        yield Pass(effect, k)

    return handler


def writer():
    """Writer handler: collects Tell(message) into a log list.

    The log is returned as the handler's result when the body completes.
    Access via the handler's return value, or inspect handler_log after run.
    """
    log = []

    @do
    def handler(effect, k):
        if isinstance(effect, WriterTellEffect):
            log.append(effect.msg)
            result = yield Resume(k, None)
            return result
        yield Pass(effect, k)

    handler.log = log  # expose for inspection
    return handler


@do
def try_handler(effect, k):
    """Try handler: catches errors from Try(program) and returns Ok/Err.

    Captures inner handlers (between body and try_handler) via GetHandlers
    and reinstalls them around the Try program, so effects from the program
    can reach handlers at any position in the chain.

    Usage:
        WithHandler(try_handler, body)
        result = yield Try(some_program)  # Ok(value) or Err(error)
    """
    if isinstance(effect, Try):
        from doeff_vm import Ok, Err
        from doeff.program import WithHandler as WH
        from doeff.handler_utils import get_inner_handlers

        inner_hs = yield get_inner_handlers(k)

        @do
        def attempt():
            prog = effect.program
            # Reinstall inner handlers + try_handler itself so nested
            # Try effects and inner-handler effects are reachable.
            for h in inner_hs:
                prog = WH(h, prog)
            prog = WH(try_handler, prog)
            try:
                value = yield prog
                return Ok(value)
            except Exception as e:
                return Err(e)
        result = yield Resume(k, (yield attempt()))
        return result
    yield Pass(effect, k)


def slog_handler():
    """Structured log handler: collects Slog messages.

    Returns a handler with a .log attribute containing collected entries.
    Each entry is a dict with 'msg' and all kwargs.
    """
    log = []

    @do
    def handler(effect, k):
        if isinstance(effect, Slog):
            entry = {"msg": effect.msg, **effect.kwargs}
            log.append(entry)
            result = yield Resume(k, None)
            return result
        yield Pass(effect, k)

    handler.log = log
    return handler


@do
def local_handler(effect, k):
    """Local handler: scoped env override with pass-on-miss semantics.

    Installs a scope reader that handles Ask for overridden keys only,
    passing non-overridden keys through to outer handlers (reader, lazy_ask).

    OCaml 5 semantics: the inner program must run with the same handler
    chain that was in scope when Local was performed. We capture inner
    handlers from the continuation (like the scheduler does for Spawn)
    and reinstall them around the inner program.

    Usage:
        WithHandler(local_handler, body)
    """
    if isinstance(effect, Local):
        from doeff.program import WithHandler as WH
        from doeff.handler_utils import get_inner_handlers
        overrides = effect.env

        # Capture inner handlers from continuation (between Local site
        # and this handler) so the inner program sees the same chain.
        inner_handlers = yield get_inner_handlers(k)

        @do
        def scope_reader(inner_effect, inner_k):
            if isinstance(inner_effect, Ask) and inner_effect.key in overrides:
                return (yield Resume(inner_k, overrides[inner_effect.key]))
            yield Pass(inner_effect, inner_k)

        # Reinstall inner handlers + local_handler itself (for nested Locals),
        # then scope_reader innermost
        prog = effect.program
        for h in inner_handlers:
            prog = WH(h, prog)
        prog = WH(local_handler, prog)
        prog = WH(scope_reader, prog)

        inner_result = yield prog
        return (yield Resume(k, inner_result))
    yield Pass(effect, k)


@do
def listen_handler(effect, k):
    """Listen handler: collects effects of specified types during program execution.

    OCaml 5 semantics: reinstall inner handlers so the inner program
    sees the same handler chain as when Listen was performed.

    Usage:
        WithHandler(listen_handler, body)
    """
    if isinstance(effect, Listen):
        from doeff.program import WithHandler as WH
        from doeff.handler_utils import get_inner_handlers
        collected = []
        types_to_collect = effect.types or (WriterTellEffect,)

        inner_handlers = yield get_inner_handlers(k)

        @do
        def observer_handler(inner_effect, inner_k):
            if isinstance(inner_effect, tuple(types_to_collect)):
                collected.append(inner_effect)
            yield Pass(inner_effect, inner_k)

        prog = effect.program
        for h in inner_handlers:
            prog = WH(h, prog)
        prog = WH(observer_handler, prog)

        inner_result = yield prog
        result = yield Resume(k, (inner_result, collected))
        return result
    yield Pass(effect, k)


def await_handler():
    """Await handler: runs async coroutines via a background thread with asyncio.

    Uses ExternalPromise to bridge async into the scheduler.
    Requires scheduler to be installed.
    """
    from doeff_core_effects.scheduler import CreateExternalPromise, Wait
    import asyncio
    import threading

    # Shared event loop running in a background thread
    _loop = [None]
    _lock = threading.Lock()

    def _get_loop():
        with _lock:
            if _loop[0] is None or _loop[0].is_closed():
                loop = asyncio.new_event_loop()
                t = threading.Thread(target=loop.run_forever, daemon=True)
                t.start()
                _loop[0] = loop
            return _loop[0]

    @do
    def handler(effect, k):
        if isinstance(effect, Await):
            ep = yield CreateExternalPromise()
            loop = _get_loop()

            async def run_coro():
                try:
                    result = await effect.coroutine
                    ep.complete(result)
                except Exception as e:
                    ep.fail(e)

            asyncio.run_coroutine_threadsafe(run_coro(), loop)
            value = yield Wait(ep.future)
            result = yield Resume(k, value)
            return result
        yield Pass(effect, k)

    return handler


def _missing_key_message(key):
    """Build an actionable error message for a missing Ask key."""
    return (
        f"Ask: key not found: {key!r}\n"
        f"\n"
        f"To provide this key, use one of:\n"
        f"  uv run doeff run --set {key}=VALUE ...          # inline key-value\n"
        f"  uv run doeff run --env myapp.module.env ...     # merge an env dict/Program[dict]\n"
        f"  uv run doeff run --interpreter myapp.interp ... # custom interpreter with env baked in\n"
        f"  uv run doeff run -c '...' --set {key}=VALUE     # with inline code (heredoc supported)\n"
        f"\n"
        f"Use {{import.path}} in --set to import a symbol: --set {key}={{myapp.impl}}\n"
        f"See: uv run doeff run --help"
    )


def lazy_ask(env=None):
    """Lazy Ask handler — replaces reader per SPEC-EFF-001.

    Handles Ask, Local, and lazy program evaluation. Takes env directly —
    no separate reader handler needed.

    Ask resolution order:
    1. Local overrides (from active Local scopes)
    2. Base env (passed to lazy_ask())
    3. KeyError if not found

    If the resolved value is a Program (Expand node from @do), it is
    evaluated lazily with caching. Concurrent asks for the same key
    coordinate via per-key semaphore.

    Local creates a new handler scope with merged env/overrides and an
    isolated scope cache for override-dependent entries.

    Cache isolation:
    - shared_cache: entries whose deps don't intersect any active override keys.
      Shared across all scopes and spawned tasks.
    - scope_cache: per-Local entries whose deps intersect override keys.
      Isolated per Local scope; tasks spawned inside the same scope share it.

    Requires scheduler (for semaphores) to be installed as an outer handler.
    """
    if env is None:
        env = {}

    from doeff.program import Expand, ResumeThrow, WithHandler as WH
    from doeff_core_effects.scheduler import (
        CreateSemaphore, AcquireSemaphore, ReleaseSemaphore,
    )

    shared_cache = {}       # key → value (override-independent entries)
    shared_deps = {}        # key → frozenset of dep keys
    eval_stack = []         # stack of dep-tracking sets for nested evals
    sems = {}               # key → Semaphore handle

    def _make_handler(effective_env, override_keys=frozenset()):
        """Create a handler with the given effective env (base + overrides)."""
        scope_cache = {}    # key → value (override-dependent, isolated per scope)
        scope_deps = {}     # key → frozenset of dep keys

        def _cache_lookup(key):
            """Check scope cache, then shared cache (validated against overrides)."""
            if key in scope_cache:
                return scope_cache[key], scope_deps.get(key, frozenset())
            if key in shared_cache:
                deps = shared_deps.get(key, frozenset())
                if not (deps & override_keys):
                    return shared_cache[key], deps
            return None, None

        def _cache_store(key, value, deps):
            """Store in scope or shared cache based on override dependency."""
            if deps & override_keys:
                scope_cache[key] = value
                scope_deps[key] = deps
            else:
                shared_cache[key] = value
                shared_deps[key] = deps

        @do
        def handler(effect, k):
            if isinstance(effect, Ask):
                # Track as dependency if inside a lazy evaluation
                if eval_stack:
                    eval_stack[-1].add(effect.key)

                # Resolve from effective env (overrides + base)
                if effect.key in effective_env:
                    raw = effective_env[effect.key]
                else:
                    return (yield ResumeThrow(
                        k, KeyError(_missing_key_message(effect.key))
                    ))

                # Plain value — resume directly
                if not isinstance(raw, Expand):
                    return (yield Resume(k, raw))

                # Cache lookup (scope then shared)
                cached_val, cached_dep = _cache_lookup(effect.key)
                if cached_val is not None:
                    if eval_stack:
                        eval_stack[-1].update(cached_dep)
                    return (yield Resume(k, cached_val))

                # Create per-key semaphore on first lazy access
                if effect.key not in sems:
                    sem = yield CreateSemaphore(1)
                    sems[effect.key] = sem

                yield AcquireSemaphore(sems[effect.key])

                # Double-check after acquiring
                cached_val, cached_dep = _cache_lookup(effect.key)
                if cached_val is not None:
                    yield ReleaseSemaphore(sems[effect.key])
                    if eval_stack:
                        eval_stack[-1].update(cached_dep)
                    return (yield Resume(k, cached_val))

                # Evaluate the program under this handler so effects
                # flow through lazy_ask and see the current env.
                eval_stack.append(set())
                error = None
                value = None
                try:
                    value = yield WH(handler, raw)
                except Exception as e:
                    error = e

                deps = frozenset(eval_stack.pop() if eval_stack else set())
                yield ReleaseSemaphore(sems[effect.key])

                if error is not None:
                    return (yield ResumeThrow(k, error))

                _cache_store(effect.key, value, deps)
                if eval_stack:
                    eval_stack[-1].update(deps)
                return (yield Resume(k, value))

            elif isinstance(effect, Local):
                # Create a new handler with merged env and fresh scope cache
                merged = {**effective_env, **effect.env}
                merged_overrides = override_keys | frozenset(effect.env.keys())
                inner_handler = _make_handler(merged, merged_overrides)

                prog = effect.program

                error = None
                inner_result = None
                try:
                    inner_result = yield WH(inner_handler, prog)
                except Exception as e:
                    error = e

                if error is not None:
                    return (yield ResumeThrow(k, error))
                return (yield Resume(k, inner_result))

            yield Pass(effect, k)

        return handler

    return _make_handler(dict(env))


