"""scripted_process.hy の公開面の型(子 process の台本の答え手 — 型検査のための宣言・実行時は scripted_process.hy を読む・agora-redesign #2197)。

- 台本の世界 ProcessScript と命令 ScriptedCommand は defrecord(凍った dataclass・名の引数だけ)。
- scripted-process-handler(Python の名 scripted_process_handler)は台本を受け、本文の Program に被せる関数を返す
  (defhandler の展開と同じ形: 本文の答えの型をそのまま運ぶ WithHandler)。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が検める。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program
from doeff_core_effects.process_effects import (
    EnvEntry,
    EnvMode,
    InterpreterFacts,
    ModuleFound,
    ProcessNotStarted,
    ProcessOutcome,
    RunProcess,
)

_A = TypeVar("_A")

@dataclass(frozen=True, kw_only=True)
class ScriptedCommand:
    name: str
    run: Callable[[tuple[ScriptedCommand, ...], RunProcess], Program[ProcessOutcome, Any]]

@dataclass(frozen=True, kw_only=True)
class ProcessScript:
    commands: tuple[ScriptedCommand, ...]
    env: tuple[EnvEntry, ...] = ()
    work_root: str = "/work/jobs"
    alive: frozenset[int] = ...
    interpreter: InterpreterFacts = ...
    modules: tuple[ModuleFound, ...] = ()

SCRIPTED_FIRST_PID: int
SCRIPTED_STOPPED_CODE: int
SCRIPTED_RUNNING: str

def scripted_child_env(
    inherited: tuple[EnvEntry, ...], env: tuple[EnvEntry, ...] | None, env_mode: EnvMode, env_drop: tuple[str, ...]
) -> Program[tuple[EnvEntry, ...] | None, Any]: ...
def run_scripted(commands: tuple[ScriptedCommand, ...], request: RunProcess) -> Program[ProcessOutcome, Any]: ...
def scripted_open_outputs(paths: tuple[str | None, ...]) -> Program[ProcessNotStarted | None, Any]: ...
def scripted_append_outputs(
    stdout_path: str | None, stderr_path: str | None, outcome: ProcessOutcome
) -> Program[None, Any]: ...
def scripted_executable_at(commands: tuple[ScriptedCommand, ...], path: str) -> Program[bool, Any]: ...

class _ScriptedProcessHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

def scripted_process_handler(script: ProcessScript) -> _ScriptedProcessHandler: ...
