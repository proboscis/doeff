# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = checkout_git_script.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from functools import partial as partial
from doeff_core_effects.process_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects.process_effects import RunProcess as RunProcess
from doeff_core_effects.scripted_process import ScriptedCommand as ScriptedCommand
NOT_A_REPOSITORY: int
BAD_USAGE: int
NO_SUCH_REMOTE: int
UNKNOWN_REV: int
COMMIT_PEEL: str

@dataclass(frozen=True, kw_only=True)
class GitRemote:
    name: str
    url: str

@dataclass(frozen=True, kw_only=True)
class GitRev:
    name: str
    sha: str
    pushed: tuple[str, ...] = ...

@dataclass(frozen=True, kw_only=True)
class GitCheckout:
    path: str
    head: str
    remotes: tuple[GitRemote, ...] = ...
    dirty: bool = False
    pushed: tuple[str, ...] = ...
    members: tuple[str, ...] = ...
    revs: tuple[GitRev, ...] = ...

def answered(stdout: str) -> _Program[ProcessOutcome, object]:
    ...

def refused(code: int, message: str) -> _Program[ProcessOutcome, object]:
    ...

def checkout_of(checkouts: tuple, path: str) -> _Program[GitCheckout | None, object]:
    ...

def commit_of(checkout: GitCheckout, rev: str) -> _Program[str | None, object]:
    ...

def pushed_of(checkout: GitCheckout, sha: str) -> _Program[tuple, object]:
    ...

def verified(checkout: GitCheckout, peeled: str) -> _Program[ProcessOutcome, object]:
    ...

def containing(checkout: GitCheckout, sha: str, pattern: str) -> _Program[ProcessOutcome, object]:
    ...

def git_answer(checkouts: tuple, commands: tuple, request: RunProcess) -> _Program[ProcessOutcome, object]:
    ...

def git_command(checkouts: tuple) -> ScriptedCommand:
    ...
