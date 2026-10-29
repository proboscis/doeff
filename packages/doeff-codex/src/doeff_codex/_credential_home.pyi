# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _credential_home.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from pathlib import Path as Path
CREDENTIAL_HOMES_DIR: str
AUTH_FILE: str
LINKED_FILES: tuple[str, ...]
SESSIONS_DIR: str
SQLITE_HOME_ENV: str
STATE_DB: str
STATE_DB_LOCK: str
STATE_DB_BUSY_SECONDS: float

def open_credential_home(base: str, auth_json: str) -> _Program[str, object]:
    ...

def credential_env(env: dict, base: str, home: str) -> _Program[dict, object]:
    ...

def repoint_state_db(base: str) -> _Program[int, object]:
    ...

def state_db_claim(base: str, block: bool) -> _Program[int | bool, object]:
    ...

def state_db_release(claim: int | bool | None) -> _Program[None, object]:
    ...

def close_credential_home(home: str | None) -> _Program[None, object]:
    ...
