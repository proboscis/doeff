# doeff-notify

Provider-agnostic notification effects for `doeff`.

`doeff-notify` lets programs declare notification intent while handlers decide delivery backend.

## Effects

- `Notify`: send a notification.
- `NotifyThread`: post a follow-up message to an existing thread.
- `Acknowledge`: wait for or query acknowledgment state.

## Reference Handlers

- `console_handler`: prints notifications to stdout.
- `testing_handler`: captures notifications in-memory for assertions.
- `log_handler`: emits notification payloads through `Tell` for logging pipelines.

## Usage

```python
from doeff import do, run
from doeff_notify import Notify
from doeff_notify import console_handler
from doeff_notify import Urgency

@do
def workflow():
    result = yield Notify(
        title="Deploy",
        message="Deployment completed",
        urgency=Urgency.LOW,
    )
    return result

# The reference handlers are `@handler` functions: apply one to a program.
run(console_handler(workflow()))
```

## Multi-Channel Stacking Example

```python
from doeff import do, run
from doeff_core_effects.handlers import state, writer
from doeff_notify import Notify
from doeff_notify import console_handler, log_handler
from doeff_notify import Urgency

@do
def workflow():
    yield Notify(
        title="Build failed",
        message="main branch CI is red",
        urgency=Urgency.HIGH,
    )

# log_handler emits `Tell`, so a writer (and the state it stores into) must
# surround it.
run(
    state()(
        writer(
            console_handler(
                log_handler(workflow()),
            ),
        ),
    ),
)
```

This pattern keeps your workflow backend-agnostic while enabling stacked handler strategies.
