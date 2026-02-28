"""Secret access helpers."""


from typing import Any

from doeff import Await, EffectGenerator, Tell, do

from .client import SecretManagerClient, get_secret_manager_client


def _ensure_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    raise TypeError("Secret payload is not bytes")


@do
def access_secret(
    secret_id: str,
    *,
    version: str = "latest",
    project: str | None = None,
    decode: bool = True,
    encoding: str = "utf-8",
) -> EffectGenerator[str | bytes]:
    """Fetch a secret value from Google Secret Manager.

    Parameters
    ----------
    secret_id:
        Identifier of the secret inside the project.
    version:
        Version identifier (``"latest"`` by default).
    project:
        Optional project override. When omitted the helper uses the project
        associated with the cached client.
    decode:
        When ``True`` (default) the secret payload is decoded with ``encoding``.
        Set to ``False`` to receive raw bytes.
    encoding:
        Text encoding to use when ``decode`` is ``True``.
    """

    client: SecretManagerClient = yield get_secret_manager_client()
    resolved_project = project or client.project
    if not resolved_project:
        yield Tell(
            "Secret Manager project missing. Provide 'secret_manager_project' or pass project=..."
        )
        raise ValueError("Secret Manager project is required to access secrets")

    secret_name = f"projects/{resolved_project}/secrets/{secret_id}/versions/{version}"
    yield Tell(
        f"Accessing Secret Manager secret secret_id={secret_id}, version={version}, project={resolved_project}"
    )

    response = yield Await(
        client.async_client.access_secret_version(request={"name": secret_name})
    )
    payload = getattr(response, "payload", None)
    if payload is None or getattr(payload, "data", None) is None:
        raise ValueError("Secret Manager response is missing payload data")

    raw_data = _ensure_bytes(payload.data)
    if decode:
        try:
            secret_value: str | bytes = raw_data.decode(encoding)
        except Exception as exc:
            raise exc
    else:
        secret_value = raw_data

    return secret_value


__all__ = ["access_secret"]
