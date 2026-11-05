"""Remember the compile of the function source that dataclass makes per type (``__create_fn__``) in the store keyed by the source contents (:mod:`code_store`).

dataclasses makes source text for the functions of each type (``__init__``, ``__repr__``, ``__eq__``, the frozen ``__setattr__`` and so on) and passes it to ``exec``,
which compiles it inside. In the check load (agora-controllers' test_emulated_screen.hy, 3798 types), almost all of the cost of making dataclasses
was this compile, with 769 distinct texts. The compile result is decided only by the text, the Python version marker (cache_tag) and the optimization level, so
the remembered code is the same as what would be compiled now (nothing goes stale -- unlike the code of Hy source, no macro check is needed).

Lookup has 2 levels: a memo within the same process (the same text is not compiled again), and an entry in the store across processes (how it is written, timestamp conventions, and
handling of broken entries live in one place, :mod:`code_store`). With the setting that does not use the store (``off``), only the in-process memo.

The wrapper replaces the name ``exec`` in the dataclasses module (dataclasses looks up the name exec in the module namespace, and falls back to builtins).
A call that is not function source is passed straight to the plain exec. It does not import dataclasses itself (to keep startup light) -- it wraps only
when already loaded (:func:`doeff_hy_bytecode_guard._loader_hooks.install` and the first read of Hy source).
"""

import builtins
import sys
from collections.abc import Mapping
from types import CellType, CodeType, ModuleType

from doeff_hy_bytecode_guard import _code_store as code_store

#: Marker of the entry key (when the shape changes, raise the trailing number -- entries with the old key no longer match).
DATACLASS_TAG = "doeff-hy/dataclass-code/1"

#: The name of the outer function that dataclasses attaches to function source text (only text with this name is remembered).
_FUNCTION_SOURCE_MARK = "def __create_fn__("

#: Code taken or compiled in this process (text -> code).
_CODES: dict[str, CodeType] = {}


def install(dataclasses_module: ModuleType) -> None:
    """Replace the name exec of the already loaded dataclasses with an exec that remembers the code of function source (only once however many times it is called)."""
    vars(dataclasses_module)["exec"] = remembering_exec


def remembering_exec(
    source: str | bytes | CodeType,
    globals_: dict[str, object] | None = None,
    locals_: Mapping[str, object] | None = None,
    /,
    *,
    closure: tuple[CellType, ...] | None = None,
) -> None:
    """In place of the exec of dataclasses -- for function-source text, exec the remembered code; anything else goes to the plain exec."""
    code = _code_of(source) if isinstance(source, str) and source.startswith(_FUNCTION_SOURCE_MARK) else source
    if closure is None:
        builtins.exec(code, globals_, locals_)
    else:
        builtins.exec(code, globals_, locals_, closure=closure)


def _code_of(source: str) -> CodeType:
    """The code for function-source text -- taken in order from the in-process memo, the store entry, and compile (after compiling, add it to the store)."""
    remembered = _CODES.get(source)
    if remembered is not None:
        return remembered
    path = _entry_path(source)
    stored = None if path is None else code_store.stored_code(path)
    # The same compile as when dataclasses passed the text to exec (file name <string>, not inheriting this module's __future__).
    code = stored if stored is not None else compile(source, "<string>", "exec", dont_inherit=True)
    if stored is None and path is not None and (not sys.dont_write_bytecode or code_store.store_is_named()):
        import marshal  # read only when adding to the store

        # Do not add to a store that cannot be written -- the store is only for speed, and the reason it cannot be written is not reported on every load (same convention as loader_hooks).
        code_store.write_entry(path, marshal.dumps(code))
    _CODES[source] = code
    return code


def _entry_path(source: str) -> str | None:
    """The entry path for function-source text (None if the setting does not use the store) -- the key is the marker, the Python version marker, the optimization level, and the text contents."""
    store = code_store.store_dir()
    if store is None:
        return None
    parts = (DATACLASS_TAG, sys.implementation.cache_tag or "", str(sys.flags.optimize))
    return code_store.entry_path(store, code_store.content_key(parts, source.encode("utf-8")), code_store.CODE_SUFFIX)
