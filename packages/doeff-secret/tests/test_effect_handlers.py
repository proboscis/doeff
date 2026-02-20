"""Tests for doeff-secret effects and built-in handlers."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff_secret.effects import DeleteSecret, GetSecret, ListSecrets, SetSecret  # noqa: E402
from doeff_secret.handlers import env_var_handler, env_var_handlers  # noqa: E402
from doeff_secret.testing import (  # noqa: E402
    InMemorySecretStore,
    in_memory_handler,
    in_memory_handlers,
)

from doeff import WithHandler, default_handlers, do, run  # noqa: E402
from doeff.rust_vm import run_with_handler_map


def _is_ok(run_result: Any) -> bool:
    checker = run_result.is_ok
    return checker() if callable(checker) else bool(checker)


@do
def _in_memory_program():
    _ = yield SetSecret(secret_id="db-password", value="v1")
    _ = yield SetSecret(secret_id="api-key", value=b"k1")
    latest = yield GetSecret(secret_id="db-password")
    all_secrets = yield ListSecrets()
    _ = yield DeleteSecret(secret_id="api-key")
    remaining = yield ListSecrets()
    return latest, all_secrets, remaining


@do
def _read_secret(secret_id: str):
    return (yield GetSecret(secret_id=secret_id))


def test_effect_exports() -> None:
    assert GetSecret(secret_id="alpha").secret_id == "alpha"
    assert SetSecret(secret_id="alpha", value="v").secret_id == "alpha"


def test_in_memory_handlers_support_secret_crud() -> None:
    result = run_with_handler_map(
        _in_memory_program(),
        in_memory_handlers(seed_data={"seed-secret": "seed-value"}),
    )

    assert _is_ok(result)
    latest, all_secrets, remaining = result.value
    assert latest == b"v1"
    assert all_secrets == ["api-key", "db-password", "seed-secret"]
    assert remaining == ["db-password", "seed-secret"]


def test_in_memory_handler_delegates_when_stacked() -> None:
    result = run(
        WithHandler(
            env_var_handler(environ={}),
            WithHandler(
                in_memory_handler(seed_data={"db-password": "from-memory"}),
                _read_secret("db-password"),
            ),
        ),
        handlers=default_handlers(),
    )

    assert _is_ok(result)
    assert result.value == b"from-memory"


def test_env_var_handlers_resolve_normalized_secret_names() -> None:
    result = run_with_handler_map(
        _read_secret("db-password"),
        env_var_handlers(environ={"DB_PASSWORD": "from-env"}),
    )

    assert _is_ok(result)
    assert result.value == "from-env"


def test_env_var_handler_prefers_prefix_when_configured() -> None:
    env = {
        "SERVICE_DB_PASSWORD": "prefixed",
        "DB_PASSWORD": "unprefixed",
    }

    result = run(
        WithHandler(
            env_var_handler(environ=env, prefix="service"),
            _read_secret("db-password"),
        ),
        handlers=default_handlers(),
    )

    assert _is_ok(result)
    assert result.value == "prefixed"


def test_in_memory_store_is_public_for_external_mocks() -> None:
    store = InMemorySecretStore.from_seed_data(seed_data={"token": "v1"})
    assert store.get_secret("token") == b"v1"


def test_env_var_handler_uses_raw_secret_id_when_enabled() -> None:
    env = {"db-password": "raw-name"}

    result = run(
        WithHandler(
            env_var_handler(environ=env, include_raw_secret_id=True),
            _read_secret("db-password"),
        ),
        handlers=default_handlers(),
    )

    assert _is_ok(result)
    assert result.value == "raw-name"


def test_env_var_handler_can_read_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("DB_PASSWORD", "from-process-env")

    result = run(
        WithHandler(env_var_handler(environ=os.environ), _read_secret("db-password")),
        handlers=default_handlers(),
    )

    assert _is_ok(result)
    assert result.value == "from-process-env"
