from __future__ import annotations

from pathlib import Path
from typing import Any

import doeff_hy  # noqa: F401  # registers Hy import hooks for the deftest module
import tests.effects.http_request_deftest_cases as http_request_deftest
from doeff_core_effects.scheduler import scheduled

from doeff import run


def _deftest_interpreter(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
    # Pytest does not collect .hy files in this repo, so this module only invokes
    # Hy deftest-generated functions with the interpreter they require.
    if env is not None:
        raise ValueError("ARC-528 HTTP deftests do not use deftest env overrides")
    return run(scheduled(program))


def test_http_production_handler_get_slog_and_close_client() -> None:
    http_request_deftest.test_http_production_handler_get_slog_and_close_client(
        _deftest_interpreter
    )


def test_http_production_handler_post_json_body() -> None:
    http_request_deftest.test_http_production_handler_post_json_body(_deftest_interpreter)


def test_http_production_handler_redirect_flag_and_timeout() -> None:
    http_request_deftest.test_http_production_handler_redirect_flag_and_timeout(
        _deftest_interpreter
    )


def test_http_production_handler_retries_5xx_statuses() -> None:
    http_request_deftest.test_http_production_handler_retries_5xx_statuses(_deftest_interpreter)


def test_http_production_handler_retries_request_exceptions_with_timeout() -> None:
    http_request_deftest.test_http_production_handler_retries_request_exceptions_with_timeout(
        _deftest_interpreter
    )


def test_http_fixture_record_forwards_to_production_handler(tmp_path: Path) -> None:
    http_request_deftest.test_http_fixture_record_forwards_to_production_handler(
        _deftest_interpreter,
        tmp_path,
    )


def test_http_fixture_replay_errors_on_unknown_request(tmp_path: Path) -> None:
    http_request_deftest.test_http_fixture_replay_errors_on_unknown_request(
        _deftest_interpreter,
        tmp_path,
    )


def test_http_wrapper_methods_build_requests() -> None:
    http_request_deftest.test_http_wrapper_methods_build_requests(_deftest_interpreter)


def test_http_request_implementation_surface_is_hy() -> None:
    http_request_deftest.test_http_request_implementation_surface_is_hy(_deftest_interpreter)
