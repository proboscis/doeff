# doeff-notify

Provider-agnostic notification effects for `doeff`.

`doeff-notify` lets programs declare notification intent while handlers decide delivery backend.

## Effects

- `Notify`: send a notification.
- `NotifyThread`: post a follow-up message to an existing thread.
- `Acknowledge`: wait for or query acknowledgment state.

## Built-In Handlers

- `console_handler`: prints notifications to stdout.
- `testing_handler`: captures notifications in-memory for assertions.
- `log_handler`: emits notification payloads through `Tell` for logging pipelines.

## Usage

```python
from doeff import WithHandler, default_handlers, do, run
from doeff_notify.effects import Notify
from doeff_notify.handlers import console_handler
from doeff_notify.types import Urgency

@do
def workflow():
    result = yield Notify(
        title="Deploy",
        message="Deployment completed",
        urgency=Urgency.LOW,
    )
    return result

run(
    WithHandler(console_handler, workflow()),
    handlers=default_handlers(),
)
```

## Multi-Channel Stacking Example

```python
from doeff import WithHandler, default_handlers, do, run
from doeff_notify.effects import Notify
from doeff_notify.handlers import console_handler, log_handler
from doeff_notify.types import Urgency

@do
def workflow():
    yield Notify(
        title="Build failed",
        message="main branch CI is red",
        urgency=Urgency.HIGH,
    )

run(
    WithHandler(
        console_handler,
        WithHandler(log_handler, workflow()),
    ),
    handlers=default_handlers(),
)
```

This pattern keeps your workflow backend-agnostic while enabling stacked handler strategies.
