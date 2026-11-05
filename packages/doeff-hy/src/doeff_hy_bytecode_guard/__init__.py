"""Do not use stale bytecode of Hy modules whose macros have changed (parent #1290).

The bytecode of a Hy module depends not only on its source but also on the macros used in its expansion. Python's
.pyc validity check looks only at the source's modification time and size (or the source hash), so even if a macro
definition (such as doeff-hy's macros.hy) changes, the modules that use it keep running with the old expansion.

This package achieves the same correctness without touching Hy (Hy stays unchanged; the workaround lives on the
doeff side) by wrapping two hooks of Python's standard ``importlib.machinery.SourceFileLoader``:

- ``source_to_code`` (the compile hook): right after a Hy module is compiled, append one record to the end of the
  code object's constants describing the macros the expansion actually used (the providing module name, the macro
  name, and a digest of the closure of the macro function's code). The macros used are collected by swapping the
  module's macro table, for the duration of the expansion, for a table that remembers the names looked up
  (``macro_recording`` -- opened by both the read hook and the compile hook used outside imports).
  The digest covers the closure's function code, narrowed to the functions executed during that expansion
  (collected during the expansion by :mod:`doeff_hy_bytecode_guard._called_code`) and the functions that built
  values (so changing a method that the expansion never calls does not invalidate users).
  The .pyc stays in the standard format (no separate file is written next to it).
  Before that, the code's Hy gensym names are renumbered to sequential numbers in module order
  (``records.canonical_gensyms`` -- the code of the same source comes out identical regardless of compile order,
  process, or thread).
- ``get_code`` (the read hook): for a Hy module's code read from a .pyc, look up each recorded macro again in the
  current environment by its providing module name and compare digests. If even one has changed, recompile from
  source and rewrite the .pyc (the record holds no file path, so a .pyc inherited from another tree is also
  checked against the current tree's macros). It does not recompile for macros that are not used or for
  line-number-only changes.

Tools that build bytecode ahead of time outside an import compile with
:func:`doeff_hy_bytecode_guard._loader_hooks.source_to_code_as_import` (compiling inside the module, as an import
does, and attaching the record -- a .pyc without a record is recompiled by the read hook).
Whether to re-bake a .pyc inherited from a previous tree is decided by
:func:`doeff_hy_bytecode_guard._loader_hooks.bytecode_is_current` (which checks the same way as the read hook).

In the same storage (code_store), the compile of the function source that a dataclass generates per type is also
remembered by content (:mod:`doeff_hy_bytecode_guard._dataclass_code` -- compilation depends only on the statement
and the Python version, so no check is needed; most of the fixed load cost was the per-type compile).
dataclasses is wrapped only if it is already loaded (at startup in :func:`install`, and at the first read of Hy source).
It also wraps the module-body execution hook of the same module, raising the GC threshold only during the outermost
load (:mod:`doeff_hy_bytecode_guard._import_gc` -- each time loading grew the heap, the whole heap was being swept).
Before returning a Hy module's code, the read hook looks for mistakes such as forgetting to require doeff-hy's macros
or importing them, and warns with the one line to add (:mod:`doeff_hy_bytecode_guard._macro_hints` -- the
message is emitted before execution because Python's frames contain no doeff code).

It is installed in two places: at venv startup (the ``doeff_hy_bytecode_guard.pth`` distributed by doeff-hy) and
on ``import doeff_hy``. Both just call :func:`install`, which wraps only once no matter how often it is called.
It works whether it runs before or after Hy is imported (before, it goes under Hy's ``source_to_code``; after, on
top of it -- either way the list can be appended right after compile).

This package is loaded at startup of every Python in the venv, so this module and
:mod:`doeff_hy_bytecode_guard._loader_hooks` import only light standard-library modules. The record format
(hashlib, marshal) lives in :mod:`doeff_hy_bytecode_guard._records`, and how used macros are collected, digests are
built, and checks are done (dis) live in :mod:`doeff_hy_bytecode_guard._macro_use`; those are imported only when a
Hy module is read for the first time.
"""

from importlib import import_module

# The entry point exposes only the startup hook install (called by the .pth and by ``import doeff_hy``). The other
# hooks -- checks (bytecode_is_current, record_is_current_here), record building and reading (macro_recording,
# record_to_json, and others), compile outside imports (source_to_code_as_import), the type-check expansion marker
# (_expansion.TYPE_CHECK_EXPANSION), and file-state memory (_import_scope.SharedScope) -- are read only by doeff's
# own tools from the inner modules (ADR-DOE-API-001 R1, R14). install stays at the entry point because it is called
# by the .pth copied into each venv (``import doeff_hy_bytecode_guard; doeff_hy_bytecode_guard.install()`` -- a copy
# made at install time that is not rewritten when doeff is upgraded).
from doeff_hy_bytecode_guard._loader_hooks import install as install

TYPE_CHECKING = False  # do not load typing and records at startup -- type checkers read the branch on this name as true

if TYPE_CHECKING:
    # Record types (public names used in annotations -- the values are built by _loader_hooks' macro_recording, record_from_json, and loaded_record).
    from doeff_hy_bytecode_guard._macro_hints import HyMacroUseWarning as HyMacroUseWarning
    from doeff_hy_bytecode_guard._records import MacroRecord as MacroRecord

del TYPE_CHECKING  # do not make it an entry-point name (the inventory counts the names of the entry module)

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
# MacroRecord has long been a type-only name at the entry point -- when the entry point has a __getattr__, the
# inventory counts the TYPE_CHECKING names, so it is placed here so that it is also readable at runtime (records is
# not loaded at startup -- it is loaded when the name is first looked up).
_LAZY_EXPORTS: dict[str, str] = {
    "HyMacroUseWarning": "doeff_hy_bytecode_guard._macro_hints",
    "MacroRecord": "doeff_hy_bytecode_guard._records",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
