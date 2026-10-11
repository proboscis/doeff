# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = credential_home.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from pathlib import Path as Path
CREDENTIAL_HOMES_DIR: str
AUTH_FILE: str
LINKED_FILES: tuple[str, ...]
SESSIONS_DIR: str

def open_credential_home(base: str, auth_json: str) -> _Program[str, object]:
    ...

def close_credential_home(home: str | None) -> _Program[None, object]:
    ...
