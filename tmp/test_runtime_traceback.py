#!/usr/bin/env python
"""Test that runtime stack trace is shown properly."""

import os
import asyncio
from doeff import do, EffectGenerator, ProgramInterpreter, Fail, Log

# Enable debug mode for creation context
os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050


def deeply_nested_function():
    """A function that will appear in the runtime stack trace."""
    def another_level():
        def yet_another():
            # This is where the actual error occurs at runtime
            raise ValueError("Deep error from nested function")
        return yet_another()
    return another_level()


@do
def failing_program() -> EffectGenerator[str]:
    """Program that fails with an exception from nested calls."""
    yield Log("Starting program")
    try:
        deeply_nested_function()
    except ValueError as e:
        # The effect is created here, but the error occurred deeper
        yield Fail(e)
    return "never reached"


async def main():
    engine = ProgramInterpreter()
    result = await engine.run(failing_program())
    
    print("=" * 60)
    print("NON-VERBOSE DISPLAY:")
    print("=" * 60)
    print(result.display(verbose=False))
    
    print("\n" * 2)
    print("=" * 60)
    print("VERBOSE DISPLAY (should show execution stack trace):")
    print("=" * 60)
    print(result.display(verbose=True))


if __name__ == "__main__":
    asyncio.run(main())