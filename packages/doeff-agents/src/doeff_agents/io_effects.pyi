"""io_effects.hy の公開面の型(Python の読み手 = driver 層の file と検)。

defk は呼ぶと Program を返す — 呼び手は `yield`(@do)か `<-`(Hy)で bind する。
"""

from dataclasses import dataclass

from doeff import EffectBase, Program

@dataclass(frozen=True, kw_only=True)
class ProcessOutcome:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

@dataclass(frozen=True, kw_only=True)
class WhichExecutable(EffectBase):
    name: str

@dataclass(frozen=True, kw_only=True)
class HomePath(EffectBase): ...

@dataclass(frozen=True, kw_only=True)
class TempRoot(EffectBase): ...

@dataclass(frozen=True, kw_only=True)
class ProcessId(EffectBase): ...

@dataclass(frozen=True, kw_only=True)
class EnvValue(EffectBase):
    name: str

@dataclass(frozen=True, kw_only=True)
class PathExists(EffectBase):
    path: str

@dataclass(frozen=True, kw_only=True)
class ReadText(EffectBase):
    path: str

@dataclass(frozen=True, kw_only=True)
class WriteText(EffectBase):
    path: str
    text: str
    mode: int | None = None

@dataclass(frozen=True, kw_only=True)
class AppendText(EffectBase):
    path: str
    text: str

@dataclass(frozen=True, kw_only=True)
class MakeDirs(EffectBase):
    path: str
    mode: int | None = None

@dataclass(frozen=True, kw_only=True)
class TouchFile(EffectBase):
    path: str
    mode: int | None = None

@dataclass(frozen=True, kw_only=True)
class CopyFile(EffectBase):
    source: str
    target: str

@dataclass(frozen=True, kw_only=True)
class ListDir(EffectBase):
    path: str
    pattern: str = "*"

@dataclass(frozen=True, kw_only=True)
class RunProcess(EffectBase):
    argv: tuple[str, ...]
    stdin: str | None = None
    timeout: float | None = None
    cwd: str | None = None

@dataclass(frozen=True, kw_only=True)
class SpawnDetached(EffectBase):
    argv: tuple[str, ...]
    log_path: str
    cwd: str | None = None

@dataclass(frozen=True, kw_only=True)
class UnixLineRequest(EffectBase):
    socket_path: str
    payload: str
    timeout: float | None = None

@dataclass(frozen=True, kw_only=True)
class UnixConnectProbe(EffectBase):
    socket_path: str
    timeout: float

@dataclass(frozen=True, kw_only=True)
class ExecutableAt(EffectBase):
    path: str

@dataclass(frozen=True, kw_only=True)
class MonotonicTime(EffectBase): ...

@dataclass(frozen=True, kw_only=True)
class Sleep(EffectBase):
    seconds: float

def which_executable(name: str) -> Program: ...
def home_path() -> Program: ...
def temp_root() -> Program: ...
def process_id() -> Program: ...
def env_value(name: str) -> Program: ...
def path_exists(path: str) -> Program: ...
def read_text(path: str) -> Program: ...
def write_text(path: str, text: str, mode: int | None = None) -> Program: ...
def append_text(path: str, text: str) -> Program: ...
def make_dirs(path: str, mode: int | None = None) -> Program: ...
def touch_file(path: str, mode: int | None = None) -> Program: ...
def copy_file(source: str, target: str) -> Program: ...
def list_dir(path: str, pattern: str = "*") -> Program: ...
def run_process(
    argv: tuple[str, ...],
    stdin: str | None = None,
    timeout: float | None = None,
    cwd: str | None = None,
) -> Program: ...
def spawn_detached(
    argv: tuple[str, ...], log_path: str, cwd: str | None = None
) -> Program: ...
def unix_line_request(
    socket_path: str, payload: str, timeout: float | None = None
) -> Program: ...
def unix_connect_probe(socket_path: str, timeout: float) -> Program: ...
def executable_at(path: str) -> Program: ...
def monotonic_time() -> Program: ...
def sleep(seconds: float) -> Program: ...
