# doeff-google-secret-manager

Lightweight helpers that let `doeff` programs fetch secrets from Google Cloud
Secret Manager using the same effectful patterns as the rest of the ecosystem.
The integration prefers Application Default Credentials (ADC) but still allows
explicit projects or credentials to be injected via Reader/State effects for
tests and advanced deployments.

## Quick start

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

The helper looks for a preconfigured Secret Manager client in the environment
(`"secret_manager_client"`). When absent it loads ADC via `google.auth.default`,
falling back to Reader/State values such as `"secret_manager_project"`,
`"secret_manager_credentials"` or `"secret_manager_client_options"`. Secrets are
fetched through the async client and never written to the logs – only metadata
like the secret identifier and version are emitted for observability.
