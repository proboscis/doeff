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
            return (yield ResumeThrow(k, KeyError(f"Ask: key not found: {effect.key!r}")))
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


def try_handler():
    """Try handler: catches errors from Try(program) and returns Ok/Err.

    Usage:
        result = yield Try(some_program)  # Ok(value) or Err(error)
    """
    from doeff_vm import Ok, Err

    @do
    def handler(effect, k):
        if isinstance(effect, Try):
            @do
            def attempt():
                try:
                    value = yield effect.program
                    return Ok(value)
                except Exception as e:
                    return Err(e)
            result = yield Resume(k, (yield attempt()))
            return result
        yield Pass(effect, k)

    return handler


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


def local_handler():
    """Local handler: scoped env override with pass-on-miss semantics.

    Installs a scope reader that handles Ask for overridden keys only,
    passing non-overridden keys through to outer handlers (reader, lazy_ask).
    """
    from doeff.program import WithHandler as WH

    @do
    def handler(effect, k):
        if isinstance(effect, Local):
            overrides = effect.env

            @do
            def scope_reader(inner_effect, inner_k):
                if isinstance(inner_effect, Ask) and inner_effect.key in overrides:
                    return (yield Resume(inner_k, overrides[inner_effect.key]))
                yield Pass(inner_effect, inner_k)

            inner_result = yield WH(scope_reader, effect.program)
            return (yield Resume(k, inner_result))
        yield Pass(effect, k)

    return handler


def listen_handler():
    """Listen handler: collects effects of specified types during program execution."""

    @do
    def handler(effect, k):
        if isinstance(effect, Listen):
            collected = []
            types_to_collect = effect.types or (WriterTellEffect,)

            @do
            def observer_handler(inner_effect, inner_k):
                if isinstance(inner_effect, tuple(types_to_collect)):
                    collected.append(inner_effect)
                yield Pass(inner_effect, inner_k)

            from doeff.program import WithHandler
            inner_result = yield WithHandler(observer_handler, effect.program)
            result = yield Resume(k, (inner_result, collected))
            return result
        yield Pass(effect, k)

    return handler


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


def lazy_ask():
    """Lazy Ask handler with semaphore-based caching per SPEC-EFF-001.

    Intercepts Ask and Local effects:
    - Ask: if env value is a Program (Expand node from @do), evaluates lazily
      with caching. Concurrent asks for the same key coordinate via per-key
      semaphore (at most one evaluation; waiters receive cached result).
    - Local: manages env overlay and cache invalidation. Cache entries whose
      evaluation depended on overridden keys are evicted at scope boundaries.

    Dependency tracking is transitive: if service depends on db_url, and db_url
    is overridden by Local, service's cache is evicted too.

    Requires reader (for base env lookup) and scheduler (for semaphores).
    Install as the innermost handler (last in handler list).
    """
    from doeff.program import Expand, Perform, ResumeThrow, WithHandler as WH
    from doeff_core_effects.scheduler import (
        CreateSemaphore, AcquireSemaphore, ReleaseSemaphore,
    )

    cache = {}            # key → resolved value
    cache_deps = {}       # key → frozenset of all dep keys (transitive)
    eval_stack = []       # stack of dep-tracking sets for nested evaluations
    sems = {}             # key → Semaphore handle
    local_overrides = {}  # merged env overrides from active Local scopes

    @do
    def handler(effect, k):
        if isinstance(effect, Ask):
            # Track as dependency if inside a lazy evaluation
            if eval_stack:
                eval_stack[-1].add(effect.key)

            # Check Local overrides first, then fall back to reader
            if effect.key in local_overrides:
                raw = local_overrides[effect.key]
            else:
                try:
                    raw = yield effect  # re-perform to reader
                except Exception as e:
                    # Reader threw (e.g. missing key) — forward to continuation
                    return (yield ResumeThrow(k, e))

            # Plain value — resume directly
            if not isinstance(raw, Expand):
                return (yield Resume(k, raw))

            # Cache hit — propagate deps to parent and resume
            if effect.key in cache:
                if eval_stack:
                    eval_stack[-1].update(cache_deps.get(effect.key, frozenset()))
                return (yield Resume(k, cache[effect.key]))

            # Create per-key semaphore on first lazy access
            if effect.key not in sems:
                sem = yield CreateSemaphore(1)
                sems[effect.key] = sem

            yield AcquireSemaphore(sems[effect.key])

            # Double-check cache after acquiring (another task may have filled it)
            if effect.key in cache:
                yield ReleaseSemaphore(sems[effect.key])
                if eval_stack:
                    eval_stack[-1].update(cache_deps.get(effect.key, frozenset()))
                return (yield Resume(k, cache[effect.key]))

            # Evaluate the program under this handler so effects (like
            # Ask for dependencies) flow through lazy_ask and see
            # local_overrides. Same closure → shared cache/overrides/deps.
            eval_stack.append(set())
            error = None
            value = None
            try:
                value = yield WH(handler, raw)
            except Exception as e:
                error = e

            deps = eval_stack.pop() if eval_stack else set()
            yield ReleaseSemaphore(sems[effect.key])

            if error is not None:
                return (yield ResumeThrow(k, error))

            cache[effect.key] = value
            cache_deps[effect.key] = frozenset(deps)
            # Propagate deps to parent evaluation (transitive tracking)
            if eval_stack:
                eval_stack[-1].update(deps)
            return (yield Resume(k, value))

        elif isinstance(effect, Local):
            override_keys = set(effect.env.keys())

            # Save current Local overrides for these keys
            saved_overrides = {}
            for ok in override_keys:
                if ok in local_overrides:
                    saved_overrides[ok] = local_overrides[ok]

            # Apply new overrides
            local_overrides.update(effect.env)

            # Save and evict cache entries that depend on overridden keys
            saved_cache = {}
            for ck in list(cache):
                deps = cache_deps.get(ck, frozenset())
                if ck in override_keys or deps & override_keys:
                    saved_cache[ck] = (cache.pop(ck), cache_deps.pop(ck, frozenset()))

            # Evaluate inner program under this handler so effects flow
            # through lazy_ask (same closure → shared cache/overrides/deps).
            # Wrap raw EffectBase in Perform so the VM can classify the body.
            prog = effect.program
            if not hasattr(prog, 'tag'):
                prog = Perform(prog)
            error = None
            inner_result = None
            try:
                inner_result = yield WH(handler, prog)
            except Exception as e:
                error = e

            # Restore overrides
            for ok in override_keys:
                if ok in saved_overrides:
                    local_overrides[ok] = saved_overrides[ok]
                else:
                    local_overrides.pop(ok, None)

            # Evict entries created inside Local that depend on overrides
            for ck in list(cache):
                if ck not in saved_cache:
                    deps = cache_deps.get(ck, frozenset())
                    if deps & override_keys:
                        cache.pop(ck)
                        cache_deps.pop(ck, None)

            # Restore pre-Local cache entries
            for ck, (cv, cd) in saved_cache.items():
                cache[ck] = cv
                cache_deps[ck] = cd

            if error is not None:
                return (yield ResumeThrow(k, error))
            return (yield Resume(k, inner_result))

        yield Pass(effect, k)

    return handler


