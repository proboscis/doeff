#!/usr/bin/env python
"""Test display() default behavior."""

import os
import asyncio
from doeff import do, EffectGenerator, ProgramInterpreter, Fail

# Enable debug mode for creation context
os.environ["DOEFF_DEBUG"] = "true"  # noqa: PINJ050


@do 
def failing_program() -> EffectGenerator[str]:
    """Program that fails."""
    # Simulate some nested error
    try:
        raise ValueError("Deep error")
    except ValueError as e:
        yield Fail(e)
    return "never"


async def main():
    engine = ProgramInterpreter()
    result = await engine.run(failing_program())
    
    print("DEFAULT display() - should show execution trace:")
    print("=" * 60)
    print(result.display())  # Default: verbose=False
    print("\n")
    
    print("VERBOSE display(verbose=True):")
    print("=" * 60)
    print(result.display(verbose=True))


if __name__ == "__main__":
    asyncio.run(main())