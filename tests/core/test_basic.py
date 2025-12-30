"""Basic test of doeff functionality."""

import asyncio

from doeff import Get, Log, Program, ProgramInterpreter, Put, do


@do
def counter_program() -> Program[int]:
    yield Put("counter", 0)
    yield Log("Starting computation")
    count = yield Get("counter")
    yield Put("counter", count + 1)
    yield Log(f"Counter incremented to {count + 1}")
    return count + 1

async def main():
    interpreter = ProgramInterpreter()
    result = await interpreter.run_async(counter_program())

    print(f"Result: {result.result}")
    print(f"Final state: {result.state}")
    print(f"Log: {result.log}")

    # Check that it worked correctly
    assert str(result.result) == "Ok(value=1)"
    assert result.state == {"counter": 1}
    assert result.log == ["Starting computation", "Counter incremented to 1"]
    print("\nAll tests passed!")

if __name__ == "__main__":
    asyncio.run(main())
