from pathlib import Path
from typing import Any, Callable

DeftestInterpreter = Callable[[Any], Any]


def test_http_production_handler_get_slog_and_close_client(
    doeff_interpreter: DeftestInterpreter,
) -> None: ...


def test_http_production_handler_post_json_body(
    doeff_interpreter: DeftestInterpreter,
) -> None: ...


def test_http_production_handler_redirect_flag_and_timeout(
    doeff_interpreter: DeftestInterpreter,
) -> None: ...


def test_http_production_handler_retries_5xx_statuses(
    doeff_interpreter: DeftestInterpreter,
) -> None: ...


def test_http_production_handler_retries_request_exceptions_with_timeout(
    doeff_interpreter: DeftestInterpreter,
) -> None: ...


def test_http_fixture_record_forwards_to_production_handler(
    doeff_interpreter: DeftestInterpreter,
    tmp_path: Path,
) -> None: ...


def test_http_fixture_replay_errors_on_unknown_request(
    doeff_interpreter: DeftestInterpreter,
    tmp_path: Path,
) -> None: ...


def test_http_wrapper_methods_build_requests(doeff_interpreter: DeftestInterpreter) -> None: ...
