"""Handler-stack tests for the Secret Manager integration."""


import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
SECRET_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "doeff-secret" / "src"
if str(SECRET_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(SECRET_PACKAGE_ROOT))

from doeff_core_effects.effects import (  # noqa: E402 - late import follows sys.path fixture setup
    EffectBase as Effect,
)
from doeff_core_effects.handlers import (  # noqa: E402
    await_handler,
    try_handler,
    writer,
)
from doeff_core_effects.handlers import (  # noqa: E402
    state as state_handler,
)
from doeff_core_effects.scheduler import scheduled  # noqa: E402
from doeff_google_secret_manager import (  # noqa: E402
    SecretManagerClient,
    access_secret,
    get_secret_manager_client,
)

from doeff import (  # noqa: E402 - late import preserves existing import/setup order
    AskEffect,
    Pass,
    Resume,
    do,
    run,
    with_handlers,
)
from doeff import Get as StateGetEffect  # noqa: E402 - late import follows sys.path fixture setup
from doeff import (  # noqa: E402 - late import preserves existing import/setup order
    Put as StatePutEffect,
)


@dataclass
class MockSecretManagerClient:
    project: str | None
    async_client: Any


class MockSecretManagerAsyncAPI:
    def __init__(self, *, payload: bytes = b"top-secret", error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.requests: list[dict[str, Any]] = []

    async def access_secret_version(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(payload=SimpleNamespace(data=self.payload))


class FakeNotFound(Exception):  # noqa: N818 - public or fixture exception name is intentionally stable
    """Fake NotFound exception for error propagation tests."""


def _run_with_mock(program, mock_handler):
    """Run ``program`` under the mock Ask/Get/Put handler.

    ``access_secret`` also performs ``Await`` (scheduler + await bridge),
    ``Try`` and ``Tell`` (writer, whose log lives in an outer ``state``) —
    the handlers the removed ``default_handlers()`` used to supply.
    """
    return run(
        scheduled(
            with_handlers(
                [await_handler(), try_handler, state_handler(), writer, mock_handler],
                program,
            )
        )
    )


def _build_handler(
    *,
    ask_values: dict[str, Any] | None = None,
    initial_state: dict[str, Any] | None = None,
    events: list[tuple[str, str, Any]] | None = None,
):
    ask_map = {} if ask_values is None else dict(ask_values)
    state = {} if initial_state is None else dict(initial_state)
    event_log: list[tuple[str, str, Any]] = [] if events is None else events

    @do
    def mock_handler(effect: Effect, k: Any):
        if isinstance(effect, AskEffect):
            value = ask_map.get(effect.key)
            event_log.append(("ask", effect.key, value))
            return (yield Resume(k, value))
        if isinstance(effect, StateGetEffect):
            value = state.get(effect.key)
            event_log.append(("get", effect.key, value))
            return (yield Resume(k, value))
        if isinstance(effect, StatePutEffect):
            state[effect.key] = effect.value
            event_log.append(("put", effect.key, effect.value))
            return (yield Resume(k, None))
        return (yield Pass(effect, k))

    return mock_handler, state, event_log


def test_access_secret_returns_decoded_secret_with_mock_handler() -> None:
    mock_async_api = MockSecretManagerAsyncAPI(payload=b"top-secret")
    mock_client = MockSecretManagerClient(project="my-project", async_client=mock_async_api)
    mock_handler, _, _ = _build_handler(ask_values={"secret_manager_client": mock_client})

    result = _run_with_mock(access_secret("db-password"), mock_handler)

    assert result == "top-secret"
    assert mock_async_api.requests == [
        {"name": "projects/my-project/secrets/db-password/versions/latest"}
    ]


def test_access_secret_can_return_bytes_with_decode_false() -> None:
    mock_async_api = MockSecretManagerAsyncAPI(payload=b"\x00\x01\x02")
    mock_client = MockSecretManagerClient(project="my-project", async_client=mock_async_api)
    mock_handler, _, _ = _build_handler(ask_values={"secret_manager_client": mock_client})

    result = _run_with_mock(access_secret("binary-secret", decode=False, project="other-project"), mock_handler)

    assert isinstance(result, bytes)
    assert result == b"\x00\x01\x02"
    assert mock_async_api.requests == [
        {"name": "projects/other-project/secrets/binary-secret/versions/latest"}
    ]


def test_access_secret_uses_explicit_version_in_request_path() -> None:
    mock_async_api = MockSecretManagerAsyncAPI(payload=b"versioned-secret")
    mock_client = MockSecretManagerClient(project="my-project", async_client=mock_async_api)
    mock_handler, _, _ = _build_handler(ask_values={"secret_manager_client": mock_client})

    result = _run_with_mock(access_secret("api-key", version="42"), mock_handler)

    assert result == "versioned-secret"
    assert mock_async_api.requests == [{"name": "projects/my-project/secrets/api-key/versions/42"}]


def test_access_secret_propagates_not_found_error() -> None:
    not_found = FakeNotFound("secret version does not exist")
    mock_async_api = MockSecretManagerAsyncAPI(error=not_found)
    mock_client = MockSecretManagerClient(project="my-project", async_client=mock_async_api)
    mock_handler, _, _ = _build_handler(ask_values={"secret_manager_client": mock_client})

    with pytest.raises(FakeNotFound, match="does not exist"):
        _run_with_mock(access_secret("missing-secret"), mock_handler)


def test_get_secret_manager_client_initializes_and_caches_in_state() -> None:
    credentials = object()
    events: list[tuple[str, str, Any]] = []
    mock_handler, state, event_log = _build_handler(
        ask_values={
            "secret_manager_client": None,
            "secret_manager_project": "fake-project-id",
            "secret_manager_credentials": credentials,
        },
        events=events,
    )

    result = _run_with_mock(get_secret_manager_client(), mock_handler)

    assert isinstance(result, SecretManagerClient)
    assert result.project == "fake-project-id"
    assert result.credentials is credentials
    assert state["secret_manager_client"] is result
    put_events = [e for e in event_log if e[0] == "put" and e[1] == "secret_manager_client"]
    assert len(put_events) == 1


def test_get_secret_manager_client_uses_cached_state_client_without_put() -> None:
    cached_client = MockSecretManagerClient(
        project="cached-project",
        async_client=MockSecretManagerAsyncAPI(payload=b"cached"),
    )
    events: list[tuple[str, str, Any]] = []
    mock_handler, _, event_log = _build_handler(
        ask_values={"secret_manager_client": None},
        initial_state={"secret_manager_client": cached_client},
        events=events,
    )

    result = _run_with_mock(get_secret_manager_client(), mock_handler)

    assert result is cached_client
    put_events = [e for e in event_log if e[0] == "put" and e[1] == "secret_manager_client"]
    assert put_events == []
    get_events = [e for e in event_log if e[0] == "get" and e[1] == "secret_manager_client"]
    assert len(get_events) >= 1
