# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = credential_home.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from pathlib import Path as Path
from doeff_claude_code.values import LentCredential as LentCredential
CREDENTIAL_HOMES_DIR: str
CREDENTIALS_FILE: str
KEPT_DIRS: tuple[str, ...]
UNLINKED_NAMES: set[str]
CLI_SCOPES: tuple[str, ...]

def credential_document(credential: LentCredential) -> _Program[str, object]:
    ...

def write_new_file(path: Path, text: str) -> _Program[None, object]:
    ...

def open_credential_home(base: str, credential: LentCredential) -> _Program[str, object]:
    ...

def replace_credential(home: str, credential: LentCredential) -> _Program[None, object]:
    ...

def close_credential_home(home: str | None) -> _Program[None, object]:
    ...
