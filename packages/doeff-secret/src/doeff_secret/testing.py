"""Testing helpers for doeff-secret effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from doeff import Delegate, Resume

from .effects import DeleteSecret, GetSecret, ListSecrets, SetSecret

ProtocolHandler = Callable[[Any, Any], Any]
SeedValue: TypeAlias = str | bytes | Sequence[str | bytes]


def _to_bytes(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


@dataclass
class InMemorySecretStore:
    """In-memory store used by mock handlers."""

    project: str = "mock-project"
    _versions: dict[str, list[bytes]] = field(default_factory=dict)

    @classmethod
    def from_seed_data(
        cls,
        *,
        seed_data: Mapping[str, SeedValue] | None = None,
        project: str = "mock-project",
    ) -> InMemorySecretStore:
        store = cls(project=project)
        if seed_data:
            for secret_id, value in seed_data.items():
                store.seed(secret_id, value)
        return store

    def seed(self, secret_id: str, value: SeedValue) -> None:
        values: Sequence[str | bytes]
        values = (value,) if isinstance(value, str | bytes) else value
        for item in values:
            self.set_secret(secret_id, item)

    def set_secret(self, secret_id: str, value: str | bytes) -> str:
        versions = self._versions.setdefault(secret_id, [])
        versions.append(_to_bytes(value))
        version_number = len(versions)
        return f"projects/{self.project}/secrets/{secret_id}/versions/{version_number}"

    def get_secret(self, secret_id: str, version: str = "latest") -> bytes:
        versions = self._versions.get(secret_id)
        if not versions:
            raise KeyError(f"Secret not found: {secret_id}")

        if version == "latest":
            return versions[-1]

        try:
            version_index = int(version) - 1
        except ValueError as exc:
            raise KeyError(f"Unknown secret version for {secret_id!r}: {version!r}") from exc

        if version_index < 0 or version_index >= len(versions):
            raise KeyError(f"Secret version not found for {secret_id!r}: {version!r}")
        return versions[version_index]

    def list_secrets(self, filter_text: str | None = None) -> list[str]:
        secret_ids = sorted(self._versions)
        if filter_text is None:
            return secret_ids
        needle = filter_text.casefold()
        return [secret_id for secret_id in secret_ids if needle in secret_id.casefold()]

    def delete_secret(self, secret_id: str) -> None:
        if secret_id not in self._versions:
            raise KeyError(f"Secret not found: {secret_id}")
        del self._versions[secret_id]


def in_memory_handlers(
    *,
    seed_data: Mapping[str, SeedValue] | None = None,
    project: str = "mock-project",
    store: InMemorySecretStore | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Build in-memory handler map for secret effects."""

    active_store = store or InMemorySecretStore.from_seed_data(
        seed_data=seed_data,
        project=project,
    )

    def handle_get_secret(effect: GetSecret, k):
        value = active_store.get_secret(effect.secret_id, version=effect.version)
        return (yield Resume(k, value))

    def handle_set_secret(effect: SetSecret, k):
        version_name = active_store.set_secret(effect.secret_id, effect.value)
        return (yield Resume(k, version_name))

    def handle_list_secrets(effect: ListSecrets, k):
        secret_ids = active_store.list_secrets(filter_text=effect.filter)
        return (yield Resume(k, secret_ids))

    def handle_delete_secret(effect: DeleteSecret, k):
        active_store.delete_secret(effect.secret_id)
        return (yield Resume(k, None))

    return {
        GetSecret: handle_get_secret,
        SetSecret: handle_set_secret,
        ListSecrets: handle_list_secrets,
        DeleteSecret: handle_delete_secret,
    }


def in_memory_handler(
    *,
    seed_data: Mapping[str, SeedValue] | None = None,
    project: str = "mock-project",
    store: InMemorySecretStore | None = None,
) -> ProtocolHandler:
    """Build a single handler-protocol callable for stacked WithHandler usage."""

    handler_map = in_memory_handlers(seed_data=seed_data, project=project, store=store)

    def handler(effect, k):
        for effect_type, effect_handler in handler_map.items():
            if isinstance(effect, effect_type):
                return (yield from effect_handler(effect, k))
        yield Delegate()

    return handler


__all__ = [
    "InMemorySecretStore",
    "ProtocolHandler",
    "SeedValue",
    "in_memory_handler",
    "in_memory_handlers",
]
