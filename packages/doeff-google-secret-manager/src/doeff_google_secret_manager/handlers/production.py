"""Production handlers for Google Secret Manager domain effects."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from doeff_secret.effects import DeleteSecret, GetSecret, ListSecrets, SetSecret

from doeff import Delegate, Resume
from doeff_google_secret_manager.client import SecretManagerClient, get_secret_manager_client

ProtocolHandler = Callable[[Any, Any], Any]


def _to_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def _normalize_text(value: Any) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def _matches_exception(exc: Exception, needle: str) -> bool:
    normalized_needle = _normalize_text(needle)
    candidates = [_normalize_text(exc.__class__.__name__), _normalize_text(str(exc))]
    code_attr = getattr(exc, "code", None)
    if callable(code_attr):
        with suppress(Exception):
            candidates.append(_normalize_text(code_attr()))
    return any(normalized_needle and normalized_needle in candidate for candidate in candidates)


def _extract_secret_id(resource_name: str) -> str:
    return resource_name.rsplit("/", 1)[-1]


@dataclass
class _ProductionSecretRuntime:
    client: SecretManagerClient | None
    project: str | None

    def resolve_client(self):
        if self.client is None:
            self.client = yield get_secret_manager_client()
        return self.client

    def resolve_project(self, client_project: str | None) -> str:
        resolved_project = self.project or client_project
        if not resolved_project:
            raise ValueError(
                "Secret Manager project is required. Set client.project or pass project=..."
            )
        return resolved_project

    def handle_get_secret(self, effect: GetSecret, k):
        resolved_client: SecretManagerClient = yield from self.resolve_client()
        resolved_project = self.resolve_project(resolved_client.project)
        version_name = (
            f"projects/{resolved_project}/secrets/{effect.secret_id}/versions/{effect.version}"
        )
        response = resolved_client.client.access_secret_version(request={"name": version_name})
        payload = getattr(response, "payload", None)
        raw_data = getattr(payload, "data", None)
        if raw_data is None:
            raise ValueError("Secret Manager response is missing payload data")
        if not isinstance(raw_data, (bytes, bytearray)):
            raise TypeError("Secret Manager payload data must be bytes")
        return (yield Resume(k, bytes(raw_data)))

    def handle_set_secret(self, effect: SetSecret, k):
        resolved_client: SecretManagerClient = yield from self.resolve_client()
        resolved_project = self.resolve_project(resolved_client.project)
        parent = f"projects/{resolved_project}"
        secret_name = f"{parent}/secrets/{effect.secret_id}"
        self._create_secret_if_missing(resolved_client, parent, effect.secret_id)

        created_version = resolved_client.client.add_secret_version(
            request={
                "parent": secret_name,
                "payload": {"data": _to_bytes(effect.value)},
            }
        )
        version_name = getattr(created_version, "name", None)
        if isinstance(version_name, str) and version_name:
            return (yield Resume(k, version_name))
        return (yield Resume(k, secret_name))

    def _create_secret_if_missing(
        self,
        resolved_client: SecretManagerClient,
        parent: str,
        secret_id: str,
    ) -> None:
        try:
            resolved_client.client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        except Exception as exc:
            if not _matches_exception(exc, "ALREADY_EXISTS"):
                raise

    def handle_list_secrets(self, effect: ListSecrets, k):
        resolved_client: SecretManagerClient = yield from self.resolve_client()
        resolved_project = self.resolve_project(resolved_client.project)
        request: dict[str, Any] = {"parent": f"projects/{resolved_project}"}
        if effect.filter is not None:
            request["filter"] = effect.filter

        listed = resolved_client.client.list_secrets(request=request)
        secret_ids: list[str] = []
        for item in listed:
            if isinstance(item, str):
                secret_ids.append(_extract_secret_id(item))
                continue
            item_name = getattr(item, "name", None)
            if isinstance(item_name, str):
                secret_ids.append(_extract_secret_id(item_name))
        return (yield Resume(k, secret_ids))

    def handle_delete_secret(self, effect: DeleteSecret, k):
        resolved_client: SecretManagerClient = yield from self.resolve_client()
        resolved_project = self.resolve_project(resolved_client.project)
        secret_name = f"projects/{resolved_project}/secrets/{effect.secret_id}"
        resolved_client.client.delete_secret(request={"name": secret_name})
        return (yield Resume(k, None))


def production_handlers(
    *,
    client: SecretManagerClient | None = None,
    project: str | None = None,
) -> ProtocolHandler:
    """Build a protocol handler backed by Google Cloud Secret Manager."""

    runtime = _ProductionSecretRuntime(client=client, project=project)

    def handler(effect: Any, k: Any):
        if isinstance(effect, GetSecret):
            return (yield from runtime.handle_get_secret(effect, k))
        if isinstance(effect, SetSecret):
            return (yield from runtime.handle_set_secret(effect, k))
        if isinstance(effect, ListSecrets):
            return (yield from runtime.handle_list_secrets(effect, k))
        if isinstance(effect, DeleteSecret):
            return (yield from runtime.handle_delete_secret(effect, k))
        yield Delegate()

    return handler


__all__ = [
    "ProtocolHandler",
    "production_handlers",
]
