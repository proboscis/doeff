from __future__ import annotations

import inspect

from doeff import Ask, Effect, Pass, Program, Resume, WithHandler, default_handlers, do, run
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


def _line_of(function: object, needle: str) -> int:
    lines, start = inspect.getsourcelines(function)
    for offset, line in enumerate(lines):
        if needle in line:
            return start + offset
    raise AssertionError(f"failed to find {needle!r} in source")


def _handler_lines_after_effect(
    lines: list[str],
    *,
    effect_fragment: str,
    detail_fragment: str | None = None,
) -> list[str]:
    for idx, raw in enumerate(lines):
        text = raw.strip()
        if not text.startswith("yield "):
            continue
        if effect_fragment not in text:
            continue
        if detail_fragment is not None and detail_fragment not in text:
            continue

        for follow_idx in range(idx + 1, len(lines)):
            candidate = lines[follow_idx].strip()
            if candidate == "handlers:":
                block: list[str] = []
                for entry in lines[follow_idx + 1 :]:
                    entry_text = entry.strip()
                    if entry_text == "":
                        break
                    if entry_text.startswith(("→ ", "✗ ", "⇢ ", "yield ", "raise ", "handlers:")):
                        break
                    if entry_text == "(same handlers)":
                        break
                    block.append(entry_text)
                return block
            if candidate == "(same handlers)":
                return [candidate]
            if candidate.startswith(("yield ", "raise ", "→ ", "✗ ", "⇢ ")):
                break
    raise AssertionError(f"handler lines not found after effect {effect_fragment!r}")


def _first_line_index(lines: list[str], fragment: str) -> int:
    for idx, line in enumerate(lines):
        if fragment in line:
            return idx
    raise AssertionError(f"line containing {fragment!r} not found")


def _assert_default_handlers_visible(handler_lines: list[str]) -> None:
    assert handler_lines
    assert all("..." not in line for line in handler_lines)
    assert any(name in line for line in handler_lines for name in _DEFAULT_HANDLER_NAMES) or any(
        "pending" in line for line in handler_lines
    )


def _assert_basic_structure(rendered: str, *, exception_type: str) -> list[str]:
    lines = rendered.strip().splitlines()
    assert lines[0] == "doeff Traceback (most recent call last):"
    assert any("()" in line for line in lines)
    assert any("yield " in line for line in lines) or any("raise " in line for line in lines)

    handler_markers = [
        line.strip() for line in lines if line.strip() in {"handlers:", "(same handlers)"}
    ]
    if handler_markers:
        assert any("✗ " in line or "⇢ " in line for line in lines)
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

    timeout_handlers = _handler_lines_after_effect(
        lines,
        effect_fragment="Ask(",
        detail_fragment="timeout",
    )
    _assert_default_handlers_visible(timeout_handlers)

    source_file = inspect.getsourcefile(fetch_config.original_generator)
    assert source_file is not None
    expected_line = _line_of(fetch_config.original_generator, 'timeout = yield Ask("timeout")')
    assert f"{source_file}:{expected_line}" in rendered


def test_example_2_custom_handler_with_withhandler() -> None:
    @do
    def auth_handler(effect: Effect, k: object):
        if getattr(effect, "key", None) == "token":
            return (yield Resume(k, "Bearer sk-1234"))
        yield Pass()

    @do
    def rate_limiter(effect: Effect, k: object):
        if getattr(effect, "key", None) == "rate_limit":
            return (yield Resume(k, 100))
        yield Pass()

    @do
    def call_api() -> Program[None]:
        _ = yield Ask("token")
        _ = yield Ask("rate_limit")
        raise ConnectionError("timeout")

    rendered = _render_failure(WithHandler(auth_handler, WithHandler(rate_limiter, call_api())))
    _assert_basic_structure(rendered, exception_type="ConnectionError")

    assert "Ask('token')" not in rendered
    assert 'Ask("token")' not in rendered
    assert "Ask('rate_limit')" not in rendered
    assert 'Ask("rate_limit")' not in rendered
    assert "rate_limiter ✓" not in rendered
    assert "→ resumed with" not in rendered
    assert "ConnectionError: timeout" in rendered


def test_example_3_handler_throws() -> None:
    @do
    def strict_handler(effect: Effect, _k: object):
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

    handler_lines = _handler_lines_after_effect(
        lines,
        effect_fragment="Put(",
        detail_fragment="result",
    )
    assert any("strict_handler ✗" in line for line in handler_lines)

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

    handler_lines = _handler_lines_after_effect(
        lines,
        effect_fragment="Ask(",
        detail_fragment="database_url",
    )
    assert any("LazyAskHandler ⇆" in line for line in handler_lines)
    assert any("ReaderHandler ✗" in line for line in handler_lines)
    assert "MissingEnvKeyError" in rendered
    assert "database_url" in rendered


def test_example_5_handler_stack_changes() -> None:
    @do
    def my_handler(_effect: Effect, _k: object):
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

    assert "yield Put(" not in rendered
    assert "my_handler" not in rendered
    assert any("raise ValueError('inner error')" in line for line in lines)


def test_example_8_spawn_chain() -> None:
    @do
    def fetch_data(url: str) -> Program[str]:
        if False:  # pragma: no cover - keep generator trace shape stable on py3.10+
            yield Ask("__unused__")
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
    assert raise_idx > separator_indices[0]
    frame_window = lines[max(0, raise_idx - 2) : raise_idx + 1]
    assert any("leaf_worker()" in line or "batch_worker()" in line for line in frame_window)
    assert "corrupt data for item 1-2" in rendered
