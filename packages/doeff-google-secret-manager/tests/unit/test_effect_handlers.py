"""Tests for Secret Manager effects and handler map integrations."""

from __future__ import annotations

import importlib
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
SECRET_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "doeff-secret" / "src"
if str(SECRET_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(SECRET_PACKAGE_ROOT))

from doeff_google_secret_manager.client import SecretManagerClient  # noqa: E402
from doeff_google_secret_manager.handlers import mock_handlers, production_handlers  # noqa: E402
from doeff_secret.effects import DeleteSecret, GetSecret, ListSecrets, SetSecret  # noqa: E402

from doeff import do  # noqa: E402
from doeff.rust_vm import run_with_handler_map


class AlreadyExistsError(Exception):
    """Raised by fake production API when creating an existing secret."""


class _FakeSecretManagerAPI:
    def __init__(self) -> None:
        self._secrets: dict[str, list[bytes]] = {}

    def create_secret(self, *, request: dict[str, Any]) -> Any:
        parent = request["parent"]
        secret_id = request["secret_id"]
        secret_name = f"{parent}/secrets/{secret_id}"
        if secret_name in self._secrets:
            raise AlreadyExistsError(secret_name)
        self._secrets[secret_name] = []
        return SimpleNamespace(name=secret_name)

    def add_secret_version(self, *, request: dict[str, Any]) -> Any:
        secret_name = request["parent"]
        payload = request["payload"]["data"]
        if secret_name not in self._secrets:
            self._secrets[secret_name] = []
        self._secrets[secret_name].append(bytes(payload))
        version_number = len(self._secrets[secret_name])
        return SimpleNamespace(name=f"{secret_name}/versions/{version_number}")

    def access_secret_version(self, *, request: dict[str, Any]) -> Any:
        version_name = request["name"]
        secret_name, _, version = version_name.rpartition("/versions/")
        versions = self._secrets.get(secret_name)
        if not versions:
            raise KeyError(secret_name)
        payload = versions[-1] if version == "latest" else versions[int(version) - 1]
        return SimpleNamespace(payload=SimpleNamespace(data=payload))

    def list_secrets(self, *, request: dict[str, Any]) -> list[Any]:
        parent = request["parent"]
        filter_text = request.get("filter")
        names = [name for name in sorted(self._secrets) if name.startswith(f"{parent}/secrets/")]
        if filter_text:
            needle = str(filter_text).casefold()
            names = [name for name in names if needle in name.casefold()]
        return [SimpleNamespace(name=name) for name in names]

    def delete_secret(self, *, request: dict[str, Any]) -> None:
        secret_name = request["name"]
        if secret_name not in self._secrets:
            raise KeyError(secret_name)
        del self._secrets[secret_name]

    @property
    def secret_count(self) -> int:
        return len(self._secrets)


def _is_ok(run_result: Any) -> bool:
    checker = run_result.is_ok
    return bool(checker()) if callable(checker) else bool(checker)


def _is_err(run_result: Any) -> bool:
    checker = run_result.is_err
    return bool(checker()) if callable(checker) else bool(checker)


def test_effect_exports() -> None:
    sys.modules.pop("doeff_google_secret_manager.effects", None)
    sys.modules.pop("doeff_google_secret_manager.effects.secrets", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        exported_effects = importlib.import_module("doeff_google_secret_manager.effects")
    assert exported_effects.GetSecret is GetSecret
    assert exported_effects.SetSecret is SetSecret
    assert any(
        "doeff_google_secret_manager.effects is deprecated" in str(item.message) for item in caught
    )


def test_handler_exports() -> None:
    exported_handlers = importlib.import_module("doeff_google_secret_manager.handlers")
    assert exported_handlers.production_handlers is production_handlers
    assert exported_handlers.mock_handlers is mock_handlers


@do
def _mock_program():
    _ = yield SetSecret(secret_id="db-password", value="v1")
    _ = yield SetSecret(secret_id="api-key", value=b"k1")
    latest = yield GetSecret(secret_id="db-password")
    all_secrets = yield ListSecrets()
    _ = yield DeleteSecret(secret_id="api-key")
    remaining = yield ListSecrets()
    return latest, all_secrets, remaining


def test_mock_handlers_use_in_memory_store() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = run_with_handler_map(
            _mock_program(),
            mock_handlers(seed_data={"seed-secret": "seed-value"}),
        )

    assert _is_ok(result)
    latest, all_secrets, remaining = result.value
    assert latest == b"v1"
    assert all_secrets == ["api-key", "db-password", "seed-secret"]
    assert remaining == ["db-password", "seed-secret"]
    assert any(
        "doeff_google_secret_manager.handlers.mock_handlers is deprecated" in str(item.message)
        for item in caught
    )


@do
def _set_and_get_program(secret_id: str):
    _ = yield SetSecret(secret_id=secret_id, value="prod-v1")
    _ = yield SetSecret(secret_id=secret_id, value="prod-v2")
    secret_value = yield GetSecret(secret_id=secret_id, version="latest")
    filtered = yield ListSecrets(filter="prod")
    return secret_value, filtered


def test_production_handlers_wrap_client_logic_with_injected_client() -> None:
    fake_api = _FakeSecretManagerAPI()
    injected_client = SecretManagerClient(
        project="prod-project",
        credentials=None,
        client_options=None,
        extra_client_kwargs={},
    )
    injected_client._client = fake_api

    result = run_with_handler_map(
        _set_and_get_program("prod-secret"),
        production_handlers(client=injected_client),
    )

    assert _is_ok(result)
    secret_value, filtered = result.value
    assert secret_value == b"prod-v2"
    assert filtered == ["prod-secret"]
    assert fake_api.secret_count == 1


@do
def _read_swap_target():
    return (yield GetSecret(secret_id="swap-target"))


def test_handler_swapping_changes_behavior() -> None:
    fake_api = _FakeSecretManagerAPI()
    injected_client = SecretManagerClient(
        project="swap-project",
        credentials=None,
        client_options=None,
        extra_client_kwargs={},
    )
    injected_client._client = fake_api

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        mock_result = run_with_handler_map(
            _read_swap_target(),
            mock_handlers(seed_data={"swap-target": "from-mock"}),
        )
    assert _is_ok(mock_result)
    assert mock_result.value == b"from-mock"
    assert any(
        "doeff_google_secret_manager.handlers.mock_handlers is deprecated" in str(item.message)
        for item in caught
    )

    production_result = run_with_handler_map(
        _read_swap_target(),
        production_handlers(client=injected_client),
    )
    assert _is_err(production_result)
    assert isinstance(production_result.error, KeyError)
