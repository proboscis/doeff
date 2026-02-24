# doeff-secret

Provider-agnostic secret management effects for `doeff` workflows.

This package defines shared secret effects that provider packages can handle:

- `GetSecret`
- `SetSecret`
- `ListSecrets`
- `DeleteSecret`

## Install

```bash
pip install doeff-secret
```

## Unified Effects

```python
from doeff import do
from doeff_secret.effects import GetSecret, SetSecret


@do
def deploy_workflow():
    _ = yield SetSecret(secret_id="deploy-token", value="token-v1")
    token = yield GetSecret(secret_id="deploy-token")
    return token
```

## In-Memory Testing

`doeff-secret` ships with an in-memory secret store for deterministic tests.

```python
from doeff import WithHandler, default_handlers, run
from doeff_secret.testing import in_memory_handlers

handlers = in_memory_handlers(seed_data={"db-password": "mock-pass"})
result = run(
    WithHandler(handlers, deploy_workflow()),
    handlers=default_handlers(),
)
```

## Environment Variable Fallback

Use `env_var_handler` when stacking `WithHandler` wrappers.

```python
from doeff import WithHandler, default_handlers, run
from doeff_secret.handlers import env_var_handler

result = run(
    WithHandler(env_var_handler(), deploy_workflow()),
    handlers=default_handlers(),
)
```

`GetSecret(secret_id="db-password")` resolves to environment key `DB_PASSWORD` by default.
