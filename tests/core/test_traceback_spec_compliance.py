from __future__ import annotations

import inspect

from doeff import Ask, Pass, Program, Resume, WithHandler, default_handlers, do, run
from doeff.effects import Put
from doeff.effects.gather import Gather
from doeff.effects.spawn import Spawn
from doeff.traceback import attach_doeff_traceback

_DEFAULT_HANDLER_NAMES = (
    "sync_await_handler",
    "spawn_intercept_handler",
    "LazyAskHandler",
    "SchedulerHandler",
    "ResultSafeHandler",
    "WriterHandler",
    "ReaderHandler",
    "StateHandler",
)
_HANDLER_STATUS_MARKERS = {"⚡", "·", "↗", "⇆", "✓", "⇢", "✗"}


def _line_of(function: object, needle: str) -> int:
    lines, start = inspect.getsourcelines(function)
    for offset, line in enumerate(lines):
        if needle in line:
            return start + offset
    raise AssertionError(f"failed to find {needle!r} in source")


def _assert_common_trace_properties(rendered: str) -> None:
    assert rendered.startswith("doeff Traceback (most recent call last):")
    assert "/doeff/do.py:" not in rendered
    assert "created_at=" not in rendered


def _assert_default_handlers_visible(rendered: str) -> None:
    stack_lines = [
        line.strip()
        for line in rendered.splitlines()
        if line.strip().startswith("[") and line.strip().endswith("]")
    ]
    assert stack_lines
    assert all("..." not in line for line in stack_lines)

    default_rows = 0
    for line in stack_lines:
        body = line[1:-1]
        tokens = [token.strip() for token in body.split(" > ")] if body else []
        names: list[str] = []
        for token in tokens:
            if token and token[-1] in _HANDLER_STATUS_MARKERS:
                names.append(token[:-1])
            else:
                names.append(token)

        if not any(name in _DEFAULT_HANDLER_NAMES for name in names):
            continue

        try:
            first_default_index = next(
                idx for idx, name in enumerate(names) if name == _DEFAULT_HANDLER_NAMES[0]
            )
        except StopIteration:
            continue

        expected = list(_DEFAULT_HANDLER_NAMES)
        observed = names[first_default_index : first_default_index + len(expected)]
        assert observed == expected
        default_rows += 1

    assert default_rows > 0


def _render_failure(
    program: object,
    *,
    env: dict[object, object] | None = None,
    store: dict[str, object] | None = None,
) -> str:
    result = run(
        program,
        handlers=default_handlers(),
        env=env,
        store=store,
        print_doeff_trace=False,
    )
    assert result.is_err()
    doeff_tb = attach_doeff_traceback(result.error, traceback_data=result.traceback_data)
    assert doeff_tb is not None
    rendered = doeff_tb.format_default()
    print(rendered)
    _assert_common_trace_properties(rendered)
    return rendered


def test_spec_example_1_nested_program_failure() -> None:
    @do
    def fetch_config(service: str) -> Program[dict[str, object]]:
        base_url = yield Ask("base_url")
        timeout = yield Ask("timeout")
        return {"url": f"{base_url}/{service}", "timeout": timeout}

    @do
    def process_item(item_id: int) -> Program[int]:
        if item_id < 2:
            return item_id
        _ = yield fetch_config("items")
        return item_id

    @do
    def batch() -> Program[None]:
        for i in range(3):
            yield process_item(i)
        return None

    rendered = _render_failure(
        batch(),
        env={"base_url": "https://api.example.com"},
    )
    assert "batch()" in rendered
    assert "yield process_item()" in rendered
    assert "yield fetch_config()" in rendered
    assert "yield Ask(" in rendered
    assert "MissingEnvKeyError" in rendered
    assert "timeout" in rendered
    _assert_default_handlers_visible(rendered)


def test_spec_example_2_with_handler_stack_markers() -> None:
    def auth_handler(effect: object, k: object):
        if getattr(effect, "key", None) == "token":
            return (yield Resume(k, "Bearer sk-1234"))
        yield Pass()

    def rate_limiter(effect: object, k: object):
        if getattr(effect, "key", None) == "rate_limit":
            return (yield Resume(k, 100))
        yield Pass()

    @do
    def call_api() -> Program[None]:
        _ = yield Ask("token")
        _ = yield Ask("rate_limit")
        raise ConnectionError("timeout")

    wrapped = WithHandler(auth_handler, WithHandler(rate_limiter, call_api()))
    rendered = _render_failure(wrapped)
    assert "yield Ask('rate_limit')" in rendered or 'yield Ask("rate_limit")' in rendered
    assert "rate_limiter✓" in rendered
    assert "auth_handler·" in rendered
    assert "→ resumed with Int(100)" in rendered or "→ resumed with 100" in rendered
    assert "raise ConnectionError('timeout')" in rendered
    assert "ConnectionError: timeout" in rendered
    _assert_default_handlers_visible(rendered)


def test_spec_example_3_handler_throws() -> None:
    def strict_handler(effect: object, _k: object):
        if getattr(effect, "key", None) == "result":
            value = getattr(effect, "value", None)
            if not isinstance(value, int):
                raise TypeError(f"expected int, got {type(value).__name__}")
        yield Pass()

    @do
    def main() -> Program[None]:
        config = yield Ask("config")
        yield Put("result", config)
        return None

    rendered = _render_failure(
        WithHandler(strict_handler, main()),
        env={"config": "not-an-int"},
        store={"result": 0},
    )
    assert "yield Put(" in rendered
    assert "strict_handler✗" in rendered
    assert "expected int, got str" in rendered
    assert "TypeError: expected int, got str" in rendered
    _assert_default_handlers_visible(rendered)


def test_spec_example_4_missing_env_key() -> None:
    @do
    def needs_db() -> Program[str]:
        db_url = yield Ask("database_url")
        return f"Connected to {db_url}"

    rendered = _render_failure(needs_db())
    assert "yield Ask('database_url')" in rendered or 'yield Ask("database_url")' in rendered
    assert "MissingEnvKeyError" in rendered
    assert "database_url" in rendered
    _assert_default_handlers_visible(rendered)


def test_spec_example_6_handler_return_abandons_inner_chain() -> None:
    def short_circuit_handler(effect: object, _k: object):
        if getattr(effect, "key", None) == "mode":
            return "fallback"
        yield Pass()

    @do
    def inner() -> Program[str]:
        mode = yield Ask("mode")
        yield Put("result", mode)
        return mode

    @do
    def outer() -> Program[None]:
        result = yield WithHandler(short_circuit_handler, inner())
        raise ValueError(f"Unexpected: {result}")

    rendered = _render_failure(outer(), store={"result": ""})
    assert "yield Ask(" in rendered
    assert "short_circuit_handler✓" in rendered
    assert "inner()" in rendered
    assert "raise ValueError(" in rendered
    assert "Unexpected: fallback" in rendered
    assert "ValueError: Unexpected: fallback" in rendered
    _assert_default_handlers_visible(rendered)


def test_spec_example_8_spawn_chain_during_gather() -> None:
    @do
    def fetch_data(url: str) -> Program[str]:
        if url.endswith("/item/3"):
            raise ConnectionError(f"Failed: {url} -> 500")
        return f"ok:{url}"

    @do
    def process_batch(items: list[str]) -> Program[list[str]]:
        tasks = []
        for item in items:
            task = yield Spawn(fetch_data(item))
            tasks.append(task)
        return (yield Gather(*tasks))

    @do
    def main() -> Program[None]:
        items = yield Ask("items")
        _ = yield process_batch(items)
        return None

    rendered = _render_failure(
        main(),
        env={
            "items": [
                "https://api.example.com/item/1",
                "https://api.example.com/item/2",
                "https://api.example.com/item/3",
            ]
        },
    )
    source_file = inspect.getsourcefile(process_batch.original_generator)
    assert source_file is not None
    assert "main()" in rendered
    assert "process_batch()" in rendered
    assert "── in task " in rendered
    assert "_spawn_task()" not in rendered
    assert "fetch_data()" in rendered
    assert "ConnectionError: Failed: https://api.example.com/item/3 -> 500" in rendered
    assert source_file in rendered
    _assert_default_handlers_visible(rendered)
