"""
Effect definitions for the doeff system.

This module provides the organized API for creating effects.
All effects are created as Effect instances with specific tags.
"""

from typing import Any, Awaitable, Callable, Dict, List

from doeff.types import Effect


class Effects:
    """Organized effect creation API with grouped categories."""

    class reader:
        """Reader monad effects (forced evaluation)."""

        @staticmethod
        def ask(key: str) -> Effect:
            """Ask for environment value."""
            return Effect("reader.ask", key)

        @staticmethod
        def local(env_update: Dict[str, Any], sub_program: Any) -> Effect:
            """Run sub-program with modified environment.

            Args:
                env_update: Environment updates to apply
                sub_program: A Program or a thunk that returns a Program
            """
            return Effect("reader.local", {"env": env_update, "program": sub_program})

    class state:
        """State monad effects (threaded through computation)."""

        @staticmethod
        def get(key: str) -> Effect:
            """Get value from state."""
            return Effect("state.get", key)

        @staticmethod
        def put(key: str, value: Any) -> Effect:
            """Update state value."""
            return Effect("state.put", {"key": key, "value": value})

        @staticmethod
        def modify(key: str, f: Callable[[Any], Any]) -> Effect:
            """Modify state value with function."""
            return Effect("state.modify", {"key": key, "func": f})

    class writer:
        """Writer monad effects (accumulated logs)."""

        @staticmethod
        def tell(message: Any) -> Effect:
            """Add to the log."""
            return Effect("writer.tell", message)

        @staticmethod
        def listen(sub_program: Any) -> Effect:
            """Run sub-program and return its log.

            Args:
                sub_program: A Program or a thunk that returns a Program
            """
            return Effect("writer.listen", sub_program)

    class future:
        """Future/async effects (forced evaluation)."""

        @staticmethod
        def await_(awaitable: Awaitable[Any]) -> Effect:
            """Await an async operation."""
            return Effect("future.await", awaitable)

        @staticmethod
        def parallel(*awaitables: Awaitable[Any]) -> Effect:
            """Run multiple async operations in parallel."""
            return Effect("future.parallel", awaitables)

    class result:
        """Result/error handling effects."""

        @staticmethod
        def fail(exc: Exception) -> Effect:
            """Signal failure."""
            return Effect("result.fail", exc)

        @staticmethod
        def catch(sub_program: Any, handler: Callable[[Exception], Any]) -> Effect:
            """Try sub-program with error handler.

            Args:
                sub_program: Program to try
                handler: Function to handle exceptions
            """
            return Effect("result.catch", {"program": sub_program, "handler": handler})
        
        @staticmethod
        def recover(sub_program: Any, fallback: Any) -> Effect:
            """Try sub-program, use fallback value on error.
            
            Args:
                sub_program: Program to try
                fallback: Value or Program to use on error
            """
            return Effect("result.recover", {"program": sub_program, "fallback": fallback})
        
        @staticmethod
        def retry(sub_program: Any, max_attempts: int = 3, delay_ms: int = 0) -> Effect:
            """Retry sub-program on failure.
            
            Args:
                sub_program: Program to retry
                max_attempts: Maximum number of attempts (default: 3)
                delay_ms: Delay between attempts in milliseconds (default: 0)
            """
            return Effect("result.retry", {
                "program": sub_program,
                "max_attempts": max_attempts,
                "delay_ms": delay_ms
            })

    class io:
        """IO effects (executed immediately, not deferred)."""

        @staticmethod
        def perform(action: Callable[[], Any]) -> Effect:
            """Perform an IO action."""
            return Effect("io.perform", action)
        
        @staticmethod
        def run(action: Callable[[], Any]) -> Effect:
            """Perform an IO action (alias for perform)."""
            return Effect("io.run", action)

        @staticmethod
        def print(message: str) -> Effect:
            """Print to stdout."""
            return Effect("io.print", message)

    class graph:
        """Graph tracking effects."""

        @staticmethod
        def step(value: Any, meta: Dict[str, Any] | None = None) -> Effect:
            """Track a computation step."""
            return Effect("graph.step", {"value": value, "meta": meta or {}})

        @staticmethod
        def annotate(meta: Dict[str, Any]) -> Effect:
            """Annotate the current step."""
            return Effect("graph.annotate", meta)

    class dep:
        """Dependency injection (pinjected compatible)."""

        @staticmethod
        def inject(key: str) -> Effect:
            """Request dependency injection."""
            return Effect("dep.inject", key)

    class gather:
        """Gather effects for parallel programs."""

        @staticmethod
        def gather(*programs: Any) -> Effect:
            """Gather results from multiple programs."""
            return Effect("gather.gather", programs)

        @staticmethod
        def gather_dict(programs: Dict[str, Any]) -> Effect:
            """Gather results from a dict of programs."""
            return Effect("gather.gather_dict", programs)


# ============================================
# Uppercase aliases (for backward compatibility)
# ============================================

def Ask(key: str) -> Effect:
    """Reader: Ask for environment value."""
    return Effects.reader.ask(key)


def Local(env_update: Dict[str, Any], sub_program: Any) -> Effect:
    """Reader: Run sub-program with modified environment."""
    return Effects.reader.local(env_update, sub_program)


def Get(key: str) -> Effect:
    """State: Get value from state."""
    return Effects.state.get(key)


def Put(key: str, value: Any) -> Effect:
    """State: Update state value."""
    return Effects.state.put(key, value)


def Modify(key: str, f: Callable[[Any], Any]) -> Effect:
    """State: Modify state value with function."""
    return Effects.state.modify(key, f)


def Log(message: Any) -> Effect:
    """Writer: Add to the log (alias for Tell)."""
    return Effects.writer.tell(message)


def Tell(message: Any) -> Effect:
    """Writer: Add to the log."""
    return Effects.writer.tell(message)


def Listen(sub_program: Any) -> Effect:
    """Writer: Run sub-program and return its log."""
    return Effects.writer.listen(sub_program)


def Await(awaitable: Awaitable[Any]) -> Effect:
    """Future: Await an async operation."""
    return Effects.future.await_(awaitable)


def Parallel(*awaitables: Awaitable[Any]) -> Effect:
    """Future: Run multiple async operations in parallel."""
    return Effects.future.parallel(*awaitables)


def Fail(exc: Exception) -> Effect:
    """Result: Signal failure."""
    return Effects.result.fail(exc)


def Catch(sub_program: Any, handler: Callable[[Exception], Any]) -> Effect:
    """Result: Try sub-program with error handler."""
    return Effects.result.catch(sub_program, handler)


def Recover(sub_program: Any, fallback: Any) -> Effect:
    """Result: Try sub-program, use fallback value on error."""
    return Effects.result.recover(sub_program, fallback)


def Retry(sub_program: Any, max_attempts: int = 3, delay_ms: int = 0) -> Effect:
    """Result: Retry sub-program on failure."""
    return Effects.result.retry(sub_program, max_attempts, delay_ms)


def IO(action: Callable[[], Any]) -> Effect:
    """IO: Perform an IO action."""
    return Effects.io.perform(action)


def Print(message: str) -> Effect:
    """IO: Print to stdout."""
    return Effects.io.print(message)


def Step(value: Any, meta: Dict[str, Any] | None = None) -> Effect:
    """Graph: Track a computation step."""
    return Effects.graph.step(value, meta)


def Annotate(meta: Dict[str, Any]) -> Effect:
    """Graph: Annotate the current step."""
    return Effects.graph.annotate(meta)


def Dep(key: str) -> Effect:
    """Dependency: Request dependency injection."""
    return Effects.dep.inject(key)


def Gather(*programs: Any) -> Effect:
    """Gather: Gather results from multiple programs."""
    return Effects.gather.gather(*programs)


def GatherDict(programs: Dict[str, Any]) -> Effect:
    """Gather: Gather results from a dict of programs."""
    return Effects.gather.gather_dict(programs)


# ============================================
# Lowercase aliases (for functional style)
# ============================================

ask = Ask
local = Local
get = Get
put = Put
modify = Modify
log = Log
tell = Tell
listen = Listen
await_ = Await
parallel = Parallel
fail = Fail
catch = Catch
recover = Recover
retry = Retry
io = IO
print_ = Print
step = Step
annotate = Annotate


__all__ = [
    # Main API
    "Effects",
    # Uppercase functions
    "Ask",
    "Local",
    "Get",
    "Put",
    "Modify",
    "Log",
    "Tell",
    "Listen",
    "Await",
    "Parallel",
    "Fail",
    "Catch",
    "Recover",
    "Retry",
    "IO",
    "Print",
    "Step",
    "Annotate",
    "Dep",
    "Gather",
    "GatherDict",
    # Lowercase aliases
    "ask",
    "local",
    "get",
    "put",
    "modify",
    "log",
    "tell",
    "listen",
    "await_",
    "parallel",
    "fail",
    "catch",
    "recover",
    "retry",
    "io",
    "print_",
    "step",
    "annotate",
]