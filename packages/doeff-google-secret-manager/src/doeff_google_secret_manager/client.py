"""Secret Manager client helpers integrated with doeff effects."""

from __future__ import annotations

from typing import Any

from doeff import Ask, EffectGenerator, Get, Put, Tell, Try, do

DEFAULT_SECRET_MANAGER_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/cloud-platform",)


class SecretManagerClient:
    """Lazy wrapper that exposes sync and async Secret Manager clients."""

    def __init__(
        self,
        *,
        project: str | None,
        credentials: Any | None,
        client_options: dict[str, Any] | None,
        extra_client_kwargs: dict[str, Any],
    ) -> None:
        self.project = project
        self.credentials = credentials
        self.client_options = client_options or {}
        self._extra_client_kwargs = dict(extra_client_kwargs)

        self._secretmanager_module: Any | None = None
        self._client: Any | None = None
        self._async_client: Any | None = None

    def _load_module(self):
        if self._secretmanager_module is None:
            from google.cloud import secretmanager  # Imported lazily

            self._secretmanager_module = secretmanager
        return self._secretmanager_module

    def _build_client_kwargs(self) -> dict[str, Any]:
        kwargs = dict(self._extra_client_kwargs)
        if self.credentials is not None:
            kwargs.setdefault("credentials", self.credentials)
        if self.client_options:
            kwargs.setdefault("client_options", self.client_options)
        return kwargs

    @property
    def client(self):
        """Return the synchronous Secret Manager client."""
        if self._client is None:
            module = self._load_module()
            self._client = module.SecretManagerServiceClient(**self._build_client_kwargs())
        return self._client

    @property
    def async_client(self):
        """Return the asynchronous Secret Manager client."""
        if self._async_client is None:
            module = self._load_module()
            self._async_client = module.SecretManagerServiceAsyncClient(
                **self._build_client_kwargs()
            )
        return self._async_client


@do
def get_secret_manager_client() -> EffectGenerator[SecretManagerClient]:
    """Retrieve or construct a :class:`SecretManagerClient` using ADC when available."""

    @do
    def ask(name: str):
        return (yield Ask(name))

    @do
    def ask_optional(name: str) -> EffectGenerator[Any]:
        safe_result = yield Try(ask(name))
        return safe_result.value if safe_result.is_ok() else None

    @do
    def get_state(name: str):
        return (yield Get(name))

    @do
    def get_optional(name: str) -> EffectGenerator[Any]:
        """Get state value, returning None if key doesn't exist."""
        safe_result = yield Try(get_state(name))
        return safe_result.value if safe_result.is_ok() else None

    safe_existing_client = yield Try(ask("secret_manager_client"))
    existing_client = safe_existing_client.value if safe_existing_client.is_ok() else None
    if existing_client:
        return existing_client

    existing_client = yield get_optional("secret_manager_client")
    if existing_client:
        return existing_client

    project = yield ask_optional("secret_manager_project")
    if project is None:
        project = yield get_optional("secret_manager_project")

    credentials = yield ask_optional("secret_manager_credentials")
    if credentials is None:
        credentials = yield get_optional("secret_manager_credentials")

    client_options = yield ask_optional("secret_manager_client_options")
    if client_options is None:
        client_options = yield get_optional("secret_manager_client_options")
    if client_options is not None and not isinstance(client_options, dict):
        yield Tell("secret_manager_client_options must be a dict; ignoring provided value")
        client_options = None

    extra_kwargs = yield ask_optional("secret_manager_client_kwargs")
    if extra_kwargs is None:
        extra_kwargs = yield get_optional("secret_manager_client_kwargs")
    if not isinstance(extra_kwargs, dict):
        extra_kwargs = {}

    if credentials is None or project is None:
        try:
            from google.auth import default as google_auth_default
            from google.auth.exceptions import DefaultCredentialsError
        except ModuleNotFoundError as exc:
            yield Tell(
                "google-auth is not installed; install google-auth or provide secret_manager_credentials"
            )
            raise exc

        try:
            adc_credentials, adc_project = google_auth_default(scopes=DEFAULT_SECRET_MANAGER_SCOPES)
        except DefaultCredentialsError as exc:
            yield Tell(
                "Failed to load Google Application Default Credentials for Secret Manager. "
                "Run 'gcloud auth application-default login' or supply secret_manager_credentials."
            )
            raise exc

        yield Tell("Using Google Application Default Credentials for Secret Manager")

        if credentials is None:
            credentials = adc_credentials
        if project is None:
            project = adc_project

    if not project:
        yield Tell(
            "Secret Manager project could not be determined. "
            "Set 'secret_manager_project' or configure ADC with a default project."
        )
        raise ValueError("Secret Manager project is required")

    client_instance = SecretManagerClient(
        project=project,
        credentials=credentials,
        client_options=client_options,
        extra_client_kwargs=extra_kwargs,
    )
    yield Put("secret_manager_client", client_instance)
    return client_instance


__all__ = [
    "DEFAULT_SECRET_MANAGER_SCOPES",
    "SecretManagerClient",
    "get_secret_manager_client",
]
