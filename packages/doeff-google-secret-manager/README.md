# doeff-google-secret-manager

Google Cloud Secret Manager handlers for `doeff` secret effects.

This package now focuses on provider-specific client and handler logic.
Provider-agnostic effect types live in `doeff-secret`.

## Unified Effect Imports

Use unified effects from `doeff-secret`:

```python
from doeff_secret.effects import GetSecret, SetSecret, ListSecrets, DeleteSecret
```

Legacy imports from `doeff_google_secret_manager.effects` still work, but emit
`DeprecationWarning`.

## Quick Start

```bash
gcloud auth application-default login
```

```python
from doeff import do, run_with_env
from doeff_google_secret_manager import access_secret


@do
def fetch_db_password() -> str:
    return (
        yield access_secret(
            secret_id="db-password",
            project="my-gcp-project",  # optional when ADC supplies a project
        )
    )


result = run_with_env(fetch_db_password())
print("password length:", len(result.value))
```

## Handler Map Usage

```python
from doeff import do, run_with_handler_map
from doeff_google_secret_manager.handlers import production_handlers
from doeff_secret.effects import GetSecret


@do
def workflow():
    return (yield GetSecret(secret_id="db-password"))


result = run_with_handler_map(
    workflow(),
    production_handlers(project="my-gcp-project"),
)
```

The helper looks for a preconfigured Secret Manager client in the environment
(`"secret_manager_client"`). When absent it loads ADC via `google.auth.default`,
falling back to Reader/State values such as `"secret_manager_project"`,
`"secret_manager_credentials"` or `"secret_manager_client_options"`.
