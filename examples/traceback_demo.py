"""Doeff traceback demo — what you see when programs fail.

Run with: uv run python examples/traceback_demo.py

When a program raises, ``run()`` attaches the doeff call chain to the exception as
``__doeff_traceback__``, writes the rendered "doeff Traceback" to stderr, and re-raises.
The rendering lists the ``@do`` frames that were active (outermost first) and, where an
effect was being handled, the handler chain it passed through (SPEC-TRACE-001).
``doeff.traceback.format_default(exc)`` returns the same text, for logs.

This demo shows three failures:
  1. An exception raised deep inside a batch pipeline
  2. An Ask for a key that the reader's env does not have
  3. Two custom handlers for the same effect — the innermost one answers
"""

import sys

from doeff_core_effects.handlers import reader, state, writer

from doeff import Ask, Get, Pass, Put, Resume, Tell, do, handler, run

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
    results = ()
    for i in range(5):
        results = (*results, (yield process_item(i)))
    total = yield Get("processed_count")
    yield Tell(f"Finished: {total} items processed")
    return results


@do
def needs_database():
    db_url = yield Ask("database_url")
    return f"Connected to {db_url}"


@do
def check_mode():
    mode = yield Ask("mode")
    if mode == "production":
        raise ValueError(f"Cannot run dangerous operation in {mode} mode")
    return f"Running in {mode}"


# -- custom handlers ----------------------------------------------------------


@handler
@do
def auth_handler(effect, k):
    if isinstance(effect, Ask) and effect.key == "auth_token":
        return (yield Resume(k, "Bearer sk-demo-1234"))
    yield Pass(effect, k)


@handler
@do
def production_mode(effect, k):
    if isinstance(effect, Ask) and effect.key == "mode":
        return (yield Resume(k, "production"))
    yield Pass(effect, k)


@handler
@do
def debug_mode(effect, k):
    if isinstance(effect, Ask) and effect.key == "mode":
        return (yield Resume(k, "debug"))
    yield Pass(effect, k)


# -- demo runner --------------------------------------------------------------


def run_and_report(title, program):
    """Run ``program``; when it fails, run() has already printed the doeff traceback."""
    print("=" * 72, flush=True)
    print(title, flush=True)
    print("=" * 72, flush=True)
    try:
        value = run(program)
    except (RuntimeError, KeyError, ValueError) as exc:
        sys.stderr.flush()
        print(
            f"\ncaught {type(exc).__name__} — run() printed its doeff traceback above", flush=True
        )
        return
    print(f"Unexpected success: {value}", flush=True)


def demo_error_trace():
    program = handler(auth_handler)(batch_pipeline())
    program = writer(program)
    program = state(initial={"processed_count": 0})(program)
    program = reader(env={"base_url": "https://api.example.com", "timeout": 30})(program)
    run_and_report("Batch pipeline — item 2 raises inside process_item()", program)


def demo_missing_env_key():
    run_and_report(
        "Missing env key — Ask('database_url') with an empty reader env",
        reader(env={})(needs_database()),
    )


def demo_handler_delegation_chain():
    # production_mode is installed innermost, so it answers Ask("mode") before
    # debug_mode sees it.
    program = handler(debug_mode)(handler(production_mode)(check_mode()))
    run_and_report("Handler stacking — the innermost handler answers", program)


if __name__ == "__main__":
    print("\n>>> Demo 1: Batch pipeline failure (item 2 fails)\n", flush=True)
    demo_error_trace()

    print("\n\n>>> Demo 2: Missing environment key\n", flush=True)
    demo_missing_env_key()

    print("\n\n>>> Demo 3: Handler delegation chain\n", flush=True)
    demo_handler_delegation_chain()
