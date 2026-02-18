"""Doeff traceback demo — what you see when programs fail.

Run with: uv run python examples/traceback_demo.py

Doeff has two trace systems:
  1. Error trace (automatic) — handler chain context on uncaught exceptions
  2. Step trace (opt-in)   — full effect dispatch log via run(..., trace=True)

This demo shows all three error trace formats:
  - format_chained()   : chronological handler dispatch + Python traceback
  - format_sectioned() : structured program/handler/root-cause summary
  - format_short()     : one-liner for logs
"""

from doeff import (
    Ask,
    AskEffect,
    Delegate,
    Get,
    Program,
    Put,
    Resume,
    Tell,
    WithHandler,
    default_handlers,
    do,
    run,
)


# -- programs ----------------------------------------------------------------


@do
def fetch_config(service_name):
    base_url = yield Ask("base_url")
    timeout = yield Ask("timeout")
    return {"url": f"{base_url}/{service_name}", "timeout": timeout}


@do
def process_item(item_id):
    config = yield fetch_config("items")
    yield Tell(f"Processing item {item_id} via {config['url']}")
    count = yield Get("processed_count")
    yield Put("processed_count", count + 1)
    if item_id == 2:
        raise RuntimeError(f"Connection refused: {config['url']}/item/{item_id}")
    return {"id": item_id, "status": "ok"}


@do
def batch_pipeline():
    yield Put("processed_count", 0)
    results = []
    for i in range(5):
        results.append((yield process_item(i)))
    total = yield Get("processed_count")
    yield Tell(f"Finished: {total} items processed")
    return results


# -- custom handler -----------------------------------------------------------


def auth_handler(effect, k):
    if isinstance(effect, AskEffect) and effect.key == "auth_token":
        return (yield Resume(k, "Bearer sk-demo-1234"))
    yield Delegate()


# -- demo runner --------------------------------------------------------------


def demo_error_trace():
    prog = WithHandler(auth_handler, batch_pipeline())
    result = run(
        prog,
        handlers=default_handlers(),
        env={"base_url": "https://api.example.com", "timeout": 30},
        store={"processed_count": 0},
    )

    if result.is_err():
        tb = result.error.__doeff_traceback__

        print("=" * 72)
        print("format_chained() — full chronological handler chain")
        print("=" * 72)
        print(tb.format_chained())

        print()
        print("=" * 72)
        print("format_sectioned() — structured summary")
        print("=" * 72)
        print(tb.format_sectioned())

        print()
        print("=" * 72)
        print("format_short() — one-liner for logs")
        print("=" * 72)
        print(tb.format_short())
    else:
        print(f"Unexpected success: {result.value}")


def demo_missing_env_key():
    @do
    def needs_database():
        db_url = yield Ask("database_url")
        return f"Connected to {db_url}"

    result = run(needs_database(), handlers=default_handlers())

    if result.is_err():
        tb = result.error.__doeff_traceback__
        print("=" * 72)
        print("Missing env key — Ask('database_url') with no env")
        print("=" * 72)
        print(tb.format_chained())
    else:
        print(f"Unexpected success: {result.value}")


def demo_handler_delegation_chain():
    def layer_1(effect, k):
        if isinstance(effect, AskEffect) and effect.key == "mode":
            return (yield Resume(k, "production"))
        yield Delegate()

    def layer_2(effect, k):
        if isinstance(effect, AskEffect) and effect.key == "mode":
            return (yield Resume(k, "debug"))
        yield Delegate()

    @do
    def check_mode():
        mode = yield Ask("mode")
        if mode == "production":
            raise ValueError(f"Cannot run dangerous operation in {mode} mode")
        return f"Running in {mode}"

    prog = WithHandler(layer_2, WithHandler(layer_1, check_mode()))
    result = run(prog, handlers=default_handlers())

    if result.is_err():
        tb = result.error.__doeff_traceback__
        print("=" * 72)
        print("Handler stacking — innermost handler wins")
        print("=" * 72)
        print(tb.format_chained())
    else:
        print(f"Unexpected success: {result.value}")


if __name__ == "__main__":
    print("\n>>> Demo 1: Batch pipeline failure (item 2 fails)\n")
    demo_error_trace()

    print("\n\n>>> Demo 2: Missing environment key\n")
    demo_missing_env_key()

    print("\n\n>>> Demo 3: Handler delegation chain\n")
    demo_handler_delegation_chain()
