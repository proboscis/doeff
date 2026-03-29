import threading
import time

import pytest

from doeff import CreateExternalPromise, Spawn, Wait, default_handlers, do, run
# REMOVED: from doeff import wait


@pytest.mark.skip(reason="uses removed API: wait (lowercase)")
def test_wait_effect_waits_on_task_handle() -> None:
    @do
    def child():
        return 42

    @do
    def program():
        task = yield Spawn(child())
        return (yield wait(task))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok(), result.display()
    assert result.value == 42


@pytest.mark.skip(reason="uses removed API: wait (lowercase)")
def test_wait_effect_waits_on_external_promise_future() -> None:
    @do
    def program():
        promise = yield CreateExternalPromise()

        def worker() -> None:
            time.sleep(0.01)
            promise.complete("done")

        threading.Thread(target=worker, daemon=True).start()
        return (yield wait(promise.future))

    result = run(program(), handlers=default_handlers())
    assert result.is_ok(), result.display()
    assert result.value == "done"
