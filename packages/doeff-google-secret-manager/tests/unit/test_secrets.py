"""Tests for the Secret Manager integration."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from doeff import EffectGenerator, AsyncRuntime, do  # noqa: E402
from doeff_google_secret_manager import access_secret  # noqa: E402


@pytest.fixture(autouse=True)
def stub_google_secret_manager(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Provide minimal google.* stubs so the package can be imported without dependencies."""

    requests: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:  # pragma: no cover - simple holder
            self.kwargs = kwargs

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def access_secret_version(self, request: dict[str, Any]) -> Any:
            requests.append(request)
            return SimpleNamespace(payload=SimpleNamespace(data=b"top-secret"))

    secretmanager_module = ModuleType("google.cloud.secretmanager")
    secretmanager_module.SecretManagerServiceClient = FakeClient  # type: ignore[attr-defined]
    secretmanager_module.SecretManagerServiceAsyncClient = FakeAsyncClient  # type: ignore[attr-defined]

    cloud_module = ModuleType("google.cloud")
    cloud_module.__path__ = []  # type: ignore[attr-defined]
    cloud_module.secretmanager = secretmanager_module  # type: ignore[attr-defined]

    google_module = ModuleType("google")
    google_module.__path__ = []  # type: ignore[attr-defined]
    google_module.cloud = cloud_module  # type: ignore[attr-defined]

    exceptions_module = ModuleType("google.auth.exceptions")
    exceptions_module.DefaultCredentialsError = Exception  # type: ignore[attr-defined]

    def fake_default(scopes: tuple[str, ...] | None = None):
        return (SimpleNamespace(scopes=scopes), "adc-project")

    auth_module = ModuleType("google.auth")
    auth_module.default = fake_default  # type: ignore[attr-defined]
    auth_module.exceptions = exceptions_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", secretmanager_module)
    monkeypatch.setitem(sys.modules, "google.auth", auth_module)
    monkeypatch.setitem(sys.modules, "google.auth.exceptions", exceptions_module)

    return requests


@pytest.mark.asyncio
async def test_access_secret_text(monkeypatch: pytest.MonkeyPatch, stub_google_secret_manager: list[dict[str, Any]]) -> None:
    """Secrets should be decoded to UTF-8 by default."""

    @do
    def flow() -> EffectGenerator[str]:
        return (yield access_secret("db-password"))

    runtime = AsyncRuntime()
    result = await runtime.run(
        flow(),
        env={
            "secret_manager_project": "my-project",
            "secret_manager_credentials": object(),
        }
    )

    assert result.is_ok
    assert result.value == "top-secret"
    assert stub_google_secret_manager == [
        {"name": "projects/my-project/secrets/db-password/versions/latest"}
    ]


@pytest.mark.asyncio
async def test_access_secret_bytes(stub_google_secret_manager: list[dict[str, Any]]) -> None:
    """Secrets can be returned as bytes when decode=False."""

    @do
    def flow() -> EffectGenerator[bytes]:
        return (yield access_secret("binary-secret", decode=False, project="other-project"))

    runtime = AsyncRuntime()
    result = await runtime.run(
        flow(),
        env={"secret_manager_credentials": object(), "secret_manager_project": "ignored"}
    )

    assert result.is_ok
    assert isinstance(result.value, bytes)
    assert result.value == b"top-secret"
    assert stub_google_secret_manager[-1] == {
        "name": "projects/other-project/secrets/binary-secret/versions/latest"
    }


@pytest.mark.asyncio
async def test_access_secret_uses_adc_when_project_missing(stub_google_secret_manager: list[dict[str, Any]]) -> None:
    """ADC fallback should populate the project when none is provided."""

    @do
    def flow() -> EffectGenerator[str]:
        return (yield access_secret("needs-project"))

    runtime = AsyncRuntime()
    result = await runtime.run(flow())

    assert result.is_ok
    assert stub_google_secret_manager[-1] == {
        "name": "projects/adc-project/secrets/needs-project/versions/latest"
    }
