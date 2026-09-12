"""io_fake.hy の公開面の型(検の I/O の家)。"""

from collections.abc import Callable, Iterable, Mapping

from doeff import Program

ANY_COMMAND: str

class FakeIoWorld:
    files: dict[str, str]
    modes: dict[str, int]
    dirs: set[str]
    env: dict[str, str]
    which: dict[str, str]
    processes: dict[object, object]
    sockets: dict[str, Callable[[str], str]]
    executables: set[str]
    home: str
    temp_root: str
    pid: int
    clock: float
    commands: list[tuple[tuple[str, ...], str | None, str | None]]
    spawned: list[tuple[tuple[str, ...], str, str | None]]
    requests: list[tuple[str, str]]
    sleeps: list[float]

    def __init__(
        self,
        *,
        files: Mapping[str, str] | None = None,
        dirs: Iterable[str] | None = None,
        env: Mapping[str, str] | None = None,
        which: Mapping[str, str] | None = None,
        processes: Mapping[object, object] | None = None,
        sockets: Mapping[str, Callable[[str], str]] | None = None,
        home: str = ...,
        temp_root: str = ...,
        pid: int = ...,
        executables: Iterable[str] | None = None,
    ) -> None: ...
    def remember_parents(self, path: str) -> None: ...
    def exists_p(self, path: str) -> bool: ...
    def outcome_for(self, argv: tuple[str, ...]) -> object: ...

class SpawnLedger:
    spawned: list[tuple[tuple[str, ...], str, str | None]]
    sleeps: list[float]
    pid: int

    def __init__(self, *, pid: int = ...) -> None: ...

def fake_driver_io_handler(world: FakeIoWorld) -> Callable[[Program], Program]: ...
def recorded_spawn_handler(ledger: SpawnLedger) -> Callable[[Program], Program]: ...
def run_fake_io(world: FakeIoWorld, program: Program) -> object: ...
