# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _python_bytecode.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
import types as types
from concurrent.futures import ProcessPoolExecutor as ProcessPoolExecutor
from doeff import run as run
from doeff_hy_bytecode_guard import bytecode_is_current as bytecode_is_current
from doeff_hy_bytecode_guard import source_to_code_as_import as source_to_code_as_import
from doeff_core_effects._file_effects import SourceNotCompiled as SourceNotCompiled
CHECKED_HASH_FLAGS: int

def checked_hash_pyc(code: types.CodeType, data: bytes) -> _Program[bytes, object]:
    ...

def compiled_pyc(rel: str, name: str, path: str, data: bytes) -> _Program[bytes | SourceNotCompiled, object]:
    ...

def kept_pyc(path: str, data: bytes, existing: bytes) -> _Program[bool, object]:
    ...

def pyc_path(source: str) -> str:
    ...

def compile_one(tree: str, rel: str, name: str) -> SourceNotCompiled | None:
    ...

def prepare_compile_path(tree: str, roots: tuple) -> None:
    ...

def _compile_item(item: tuple) -> SourceNotCompiled | None:
    ...

def compile_python_sources(tree: str, items: tuple, jobs: int, roots: tuple) -> tuple:
    ...
