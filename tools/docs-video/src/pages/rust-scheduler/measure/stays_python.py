# What stays in Python under both implementations: the effect classes (subclasses resolve as their parent),
# the handle classes and the exception classes.
from doeff_core_effects import (
    AcquireSemaphore,
    CreateSemaphore,
    Promise,
    ReleaseSemaphore,
    Spawn,
    Task,
    TaskCancelledError,
    Cancel,
    Wait,
    CreatePromise,
    scheduled,
)

from doeff import do, run


class CreateNamedSemaphore(CreateSemaphore):
    def __init__(self, name: str, permits: int):
        super().__init__(permits)
        self.name = name


@do
def idle(p):
    yield Wait(p.future)


@do
def main():
    sem = yield CreateNamedSemaphore("db", 2)
    yield AcquireSemaphore(sem)
    yield ReleaseSemaphore(sem)
    promise = yield CreatePromise()
    task = yield Spawn(idle(promise))
    yield Cancel(task)
    try:
        yield Wait(task)
        caught = None
    except TaskCancelledError as exc:
        caught = type(exc)
    return {
        "CreateNamedSemaphore ->": type(sem).__name__,
        "Spawn ->": f"{type(task).__module__}.{type(task).__name__}",
        "is Task": isinstance(task, Task),
        "is Promise": isinstance(promise, Promise),
        "Cancel then Wait raises": caught.__name__,
        "the same class": caught is TaskCancelledError,
    }


for impl in ("python", "rust"):
    print(f"{impl}:")
    for key, value in run(scheduled(main(), implementation=impl)).items():
        print(f"  {key} {value}")
