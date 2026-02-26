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


def _handler_tokens(stack_line: str) -> list[str]:
    stripped = stack_line.strip()
    if stripped == "[same]":
        return []
    assert stripped.startswith("[")
    assert stripped.endswith("]")
    body = stripped[1:-1]
    if body == "":
        return []
    return [token.strip() for token in body.split(" > ")]


def _token_handler_name(token: str) -> str:
    if token and token[-1] in _HANDLER_STATUS_MARKERS:
        return token[:-1]
    return token


def _token_has_handler_name(token: str, handler_name: str) -> bool:
    return _token_handler_name(token).endswith(handler_name)


def _stack_line_after_effect(
    lines: list[str],
    *,
    effect_fragment: str,
    detail_fragment: str | None = None,
) -> str:
    for idx, raw in enumerate(lines):
        text = raw.strip()
        if not text.startswith("yield "):
            continue
        if effect_fragment not in text:
            continue
        if detail_fragment is not None and detail_fragment not in text:
            continue

        for follow in lines[idx + 1 :]:
            candidate = follow.strip()
            if candidate.startswith("["):
                return candidate
            if candidate.startswith(("yield ", "raise ")):
                break
    raise AssertionError(f"stack line not found after effect {effect_fragment!r}")


def _first_line_index(lines: list[str], fragment: str) -> int:
    for idx, line in enumerate(lines):
        if fragment in line:
            return idx
    raise AssertionError(f"line containing {fragment!r} not found")


def _assert_default_handlers_visible(stack_line: str) -> None:
    tokens = _handler_tokens(stack_line)
    assert tokens, stack_line
    assert "..." not in stack_line

    names_to_marker: dict[str, str] = {}
    for token in tokens:
        if token and token[-1] in _HANDLER_STATUS_MARKERS:
            names_to_marker[token[:-1]] = token[-1]

    for handler_name in _DEFAULT_HANDLER_NAMES:
        assert handler_name in names_to_marker, stack_line
        assert names_to_marker[handler_name] in _HANDLER_STATUS_MARKERS


def _assert_basic_structure(rendered: str, *, exception_type: str) -> list[str]:
    lines = rendered.strip().splitlines()
    assert lines[0] == "doeff Traceback (most recent call last):"
    assert any("()" in line for line in lines)
    assert any("yield " in line for line in lines)

    stack_lines = [line.strip() for line in lines if line.strip().startswith("[")]
    assert stack_lines
    assert any(">" in line or line == "[same]" for line in stack_lines)
    assert any("→ resumed" in line or "✗ " in line or "⇢ " in line for line in lines)
    assert lines[-1].startswith(exception_type)
    return lines


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
    return doeff_tb.format_default()


def test_example_1_nested_program_failure() -> None:
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
    lines = _assert_basic_structure(rendered, exception_type="MissingEnvKeyError")

    assert "yield process_item(" in rendered or "yield process_item()" in rendered
    assert "yield fetch_config(" in rendered or "yield fetch_config()" in rendered
    assert "Ask('base_url')" not in rendered
    assert 'Ask("base_url")' not in rendered
    assert "<builtins.PyAsk" not in rendered
    assert " object at 0x" not in rendered

    timeout_stack = _stack_line_after_effect(
        lines,
        effect_fragment="Ask(",
        detail_fragment="timeout",
    )
    _assert_default_handlers_visible(timeout_stack)

    source_file = inspect.getsourcefile(fetch_config.original_generator)
    assert source_file is not None
    expected_line = _line_of(fetch_config.original_generator, 'timeout = yield Ask("timeout")')
    assert f"{source_file}:{expected_line}" in rendered


def test_example_2_custom_handler_with_withhandler() -> None:
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

    rendered = _render_failure(WithHandler(auth_handler, WithHandler(rate_limiter, call_api())))
    lines = _assert_basic_structure(rendered, exception_type="ConnectionError")

    rate_limit_stack = _stack_line_after_effect(
        lines,
        effect_fragment="Ask(",
        detail_fragment="rate_limit",
    )
    tokens = _handler_tokens(rate_limit_stack)
    assert tokens
    assert _token_has_handler_name(tokens[0], "rate_limiter"), rate_limit_stack
    assert any(
        _token_has_handler_name(token, "auth_handler") and token.endswith("·") for token in tokens
    )
    _assert_default_handlers_visible(rate_limit_stack)
    assert "Ask('token')" not in rendered
    assert 'Ask("token")' not in rendered
    assert "→ resumed with" in rendered
    assert "100" in rendered
    assert "ConnectionError: timeout" in rendered


def test_example_3_handler_throws() -> None:
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
    lines = _assert_basic_structure(rendered, exception_type="TypeError")

    stack_line = _stack_line_after_effect(
        lines,
        effect_fragment="Put(",
        detail_fragment="result",
    )
    assert "strict_handler✗" in stack_line

    result_line = next(line.strip() for line in lines if "raised TypeError" in line)
    assert result_line.startswith("✗ ")
    assert "strict_handler" in result_line
    assert "TypeError: expected int, got str" in rendered


def test_example_4_missing_env_key() -> None:
    @do
    def needs_db() -> Program[str]:
        db_url = yield Ask("database_url")
        return f"Connected to {db_url}"

    rendered = _render_failure(needs_db())
    lines = _assert_basic_structure(rendered, exception_type="MissingEnvKeyError")

    stack_line = _stack_line_after_effect(
        lines,
        effect_fragment="Ask(",
        detail_fragment="database_url",
    )
    assert "LazyAskHandler⇆" in stack_line
    assert "ReaderHandler✗" in stack_line
    assert "MissingEnvKeyError" in rendered
    assert "database_url" in rendered


def test_example_5_handler_stack_changes() -> None:
    def my_handler(_effect: object, _k: object):
        yield Pass()

    @do
    def inner() -> Program[None]:
        yield Put("y", 2)
        raise ValueError("inner error")

    @do
    def outer() -> Program[None]:
        yield Put("x", 1)
        _ = yield WithHandler(my_handler, inner())
        return None

    rendered = _render_failure(outer(), store={"x": 0, "y": 0})
    lines = _assert_basic_structure(rendered, exception_type="ValueError")

    outer_stack = _stack_line_after_effect(lines, effect_fragment="Put(", detail_fragment='"x"')
    inner_stack = _stack_line_after_effect(lines, effect_fragment="Put(", detail_fragment='"y"')
    assert outer_stack != inner_stack
    assert "my_handler" not in outer_stack

    inner_tokens = _handler_tokens(inner_stack)
    assert inner_tokens
    assert _token_has_handler_name(inner_tokens[0], "my_handler"), inner_stack
    _assert_default_handlers_visible(outer_stack)
    _assert_default_handlers_visible(inner_stack)


def test_example_8_spawn_chain() -> None:
    @do
    def fetch_data(url: str) -> Program[str]:
        if url.endswith("/item/3"):
            raise ConnectionError(f"Failed: {url} -> 500")
        return f"ok:{url}"

    @do
    def process_batch(items: list[str]) -> Program[list[str]]:
        tasks = []
        for item in items:
            tasks.append((yield Spawn(fetch_data(item))))
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
    lines = _assert_basic_structure(rendered, exception_type="ConnectionError")

    gather_index = _first_line_index(lines, "yield Gather(")
    boundary_index = _first_line_index(lines, "── in task ")
    child_index = _first_line_index(lines, "fetch_data(")
    assert gather_index < boundary_index < child_index
    assert "_spawn_task()" not in rendered
    assert "spawned at process_batch()" in rendered
    assert "item/3" in rendered


def test_example_9_nested_spawn() -> None:
    @do
    def leaf_worker(item_id: str) -> Program[str]:
        if item_id == "1-2":
            raise RuntimeError(f"corrupt data for item {item_id}")
        return f"ok:{item_id}"

    @do
    def batch_worker(batch_id: int) -> Program[list[str]]:
        tasks = []
        for i in range(3):
            tasks.append((yield Spawn(leaf_worker(f"{batch_id}-{i}"))))
        return (yield Gather(*tasks))

    @do
    def orchestrator() -> Program[list[list[str]]]:
        batches = []
        for b in range(2):
            batches.append((yield Spawn(batch_worker(b))))
        return (yield Gather(*batches))

    rendered = _render_failure(orchestrator())
    lines = _assert_basic_structure(rendered, exception_type="RuntimeError")

    separator_indices = [idx for idx, line in enumerate(lines) if "── in task " in line]
    assert len(separator_indices) >= 2
    assert separator_indices == sorted(separator_indices)

    gather_indices = [idx for idx, line in enumerate(lines) if "yield Gather(" in line]
    assert gather_indices
    assert gather_indices[0] < separator_indices[0]
    assert any(separator_indices[0] < idx < separator_indices[1] for idx in gather_indices)

    raise_idx = _first_line_index(lines, "raise RuntimeError(")
    assert raise_idx > separator_indices[1]
    assert "corrupt data for item 1-2" in rendered
