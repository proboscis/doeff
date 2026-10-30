# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = worktree_git_script.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_core_effects import StatPath as StatPath
from doeff_core_effects import PathStat as PathStat
from doeff_core_effects import PathKind as PathKind
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ReadText as ReadText
from doeff_core_effects import WriteText as WriteText
from doeff_core_effects import MakeDirectory as MakeDirectory
from doeff_core_effects import ListDirectory as ListDirectory
from doeff_core_effects import DirEntry as DirEntry
from doeff_core_effects import RemoveTree as RemoveTree
from doeff_core_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects import RunProcess as RunProcess
from doeff_core_effects import ScriptedCommand as ScriptedCommand
from doeff_traverse import sequence as sequence
GIT_DIR: str
ORIGIN: str
DEFAULT_BRANCH: str

@dataclass(frozen=True, kw_only=True)
class OriginBranch:
    repo: str
    branch: str

@dataclass(frozen=True, kw_only=True)
class Registration:
    name: str
    worktree: str
    branch: str

def passed(stdout: str) -> _Program[ProcessOutcome, object]:
    ...

def refused(code: int, stderr: str) -> _Program[ProcessOutcome, object]:
    ...

def sha_of(repo: str, branch: str) -> _Program[str, object]:
    ...

def kind_of(path: str) -> _Program[PathKind, object]:
    ...

def file_written(path: str, text: str) -> _Program[None, object]:
    ...

def ref_sha(main: str, ref: str) -> _Program[str | None, object]:
    ...

def registration_of(root: str, name: str) -> _Program[Registration | None, object]:
    ...

def registrations(main: str) -> _Program[tuple[Registration, ...], object]:
    ...

def porcelain_block(worktree: str, repo: str, branch: str) -> _Program[str, object]:
    ...

def listed(main: str, repo: str) -> _Program[ProcessOutcome, object]:
    ...

def pruned(main: str) -> _Program[ProcessOutcome, object]:
    ...

def fetched(main: str, repo: str, origins: tuple[OriginBranch, ...]) -> _Program[ProcessOutcome, object]:
    ...

def verified(main: str, ref: str) -> _Program[ProcessOutcome, object]:
    ...

def ancestry(main: str, repo: str, older: str, newer: str) -> _Program[ProcessOutcome, object]:
    ...

def forced(main: str, branch: str, start: str) -> _Program[ProcessOutcome, object]:
    ...

def added(main: str, repo: str, target: str, branch: str, start: str | None) -> _Program[ProcessOutcome, object]:
    ...

def worktree_git_answer(origins: tuple[OriginBranch, ...], reachable: bool, request: RunProcess) -> _Program[ProcessOutcome, object]:
    ...

def worktree_git_command(origins: tuple, reachable: bool) -> ScriptedCommand:
    ...
