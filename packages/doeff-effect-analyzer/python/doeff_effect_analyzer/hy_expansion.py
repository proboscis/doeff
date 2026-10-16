"""A Hy module expanded with Hy's own compiler, and the expanded trees kept on disk under what the
expansion depends on — not under the reader that walks them.

Macro expansion is most of an analysis' time on a cold cache (0.5-1 s per module; the screen job's
closure expands 224 modules, 212 s of 244 s). The expanded tree depends only on the source's
content, the module name, the macros the expansion used, the Hy / Python versions, the shape of the
record of those macros (``RECORD_TAG``) and the code of this module, so it is kept under a key made
of exactly those (``_ExpansionKey``). The reader (``program_effects``) keeps what it derives from a
tree (the definition index, the body nodes and facts) in entries of its own, keyed on its source as
well (``program_effects._TreeKey``); when its source changes, it reads the tree from here and
rebuilds only what it derives. Before, the tree was kept only with the derived values under the
reader's key, so every change of program_effects.py (9 changes in 10-01..10-11) expanded every
module again — the screen job's closure test took 257.3 s in the daily run after such a change
(ki-81f1c868203f).

Only the expansion and the store of its trees are here: the digest of this file is in the key
(``_expander_digest``), so a tree another version of the expansion made is never read. Hence what
lies in the store is decided here alone (design check lt-DMXJNAWWMCFNV7F1QX3GJZ574H, blind B): a
caller asks for a tree (``_expansion``) and never writes one, so a tree a reader reshaped cannot lie
under a key without the reader; and this module imports no other module of the analyzer, so no code
outside this file decides a tree. Both are kept by ``tests/python/test_hy_tree_cache.py``.
"""

import ast
import functools
import hashlib
import os
import pickle
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Read at run time only where a Hy module is expanded or its stored tree read.
    from doeff_hy_bytecode_guard.records import MacroRecord


@dataclass(frozen=True)
class _Expanded:
    """A Hy module expanded in this process, and the record of what the expansion used: the Hy
    version and every macro the expansion called, each with the digest of its code's closure (the
    helpers it calls, the values it reads, the modules it imports) — ``macro_recording``, the record
    the import side puts on the module's bytecode."""

    tree: ast.Module
    macros: "MacroRecord"


def _expand_hy(source: str, filename: str, module_name: str) -> _Expanded:
    """Expand a Hy module with Hy's own compiler so user macros become the Python AST the reader walks."""
    import hy
    import hy.compiler
    from doeff_hy_bytecode_guard import macro_recording

    # A fresh module object: expansion registers macros on it and must not
    # disturb the imported module whose globals resolve names.
    scratch = types.ModuleType(module_name)
    scratch.__file__ = filename
    with macro_recording(scratch) as recording:
        compiled = hy.compiler.hy_compile(
            hy.read_many(source, filename=filename), scratch, filename=filename, source=source
        )
    if not isinstance(compiled, ast.Module):
        raise TypeError(f"{filename}: Hy compiled to {type(compiled)!r}, expected a module")
    return _Expanded(tree=compiled, macros=recording.record(source))


# Which macros an expansion uses is known only after it (a macro expands to calls of
# others; its body calls helpers), so the key has two levels, as the import side's shared
# code store keys the code by the source and checks the record it stores with it
# (doeff_hy_bytecode_guard): ``_ExpansionKey`` names the place of a source, and in it each entry
# is named by the fingerprint of the record of the macros it was expanded with and starts
# with that record. An entry is read only when its record is this environment's
# (``record_is_current_here`` — each macro found again by its module's name and its digest
# compared; a record holds no path). Worktrees of different doeff versions keep their own
# entries side by side instead of overwriting one. The reader's entries are laid out alike.


@dataclass(frozen=True)
class _ExpansionKey:
    """What the place of an expanded tree is a function of. The file's path is not one of them:
    the expansion reads the path only for its suffix (``.hyk`` / ``.hyp`` decide whether ``defp``
    is refused or ``defk`` warned about, never the tree), nothing derived from the tree holds it,
    and the reader takes the path it reports from the module it reads
    (``_ModuleSource.filename``) — so a worktree whose file has the same content reads the entry
    another worktree wrote (agora-redesign #3598: a closure test of the screen job took 120.7 s
    in a new worktree and 5.9 s after). The reader's source is not one either
    (ki-81f1c868203f). The macros used name the entry within the place (``_entry_name``)."""

    python: str
    hy: str
    expander: str
    record_format: str
    module_name: str
    source_sha256: str

    def text(self) -> str:
        """Every field, one per line — what the place is the digest of (the reader's key adds its own)."""
        return "\n".join(
            [self.python, self.hy, self.expander, self.record_format, self.module_name, self.source_sha256]
        )

    def place(self, directory: Path) -> Path:
        """The directory of this source's trees: the digest of every field, so any change misses."""
        return directory / "expansions" / hashlib.sha256(self.text().encode("utf-8")).hexdigest()


def _expansion_key(source: str, module_name: str) -> _ExpansionKey:
    """The key of the place of the trees ``source`` expands to as ``module_name`` (see ``_ExpansionKey``)."""
    import hy
    from doeff_hy_bytecode_guard.records import RECORD_TAG

    return _ExpansionKey(
        python=sys.version,
        hy=hy.__version__,
        expander=_expander_digest(),
        record_format=RECORD_TAG,
        module_name=module_name,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


@functools.cache
def _expander_digest() -> str:
    """The digest of this module's source: how a module is expanded and how its tree is stored."""
    return f"expander={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}"


def _entry_name(macros: "MacroRecord") -> str:
    """The entry of one set of macros used: the fingerprint of their record (module names, macro
    names and digests — no path, so the same macros in another worktree name the same entry)."""
    from doeff_hy_bytecode_guard import record_fingerprint

    return f"{record_fingerprint(macros)}.pickle"


def _expansion(source: str, filename: str, module_name: str, directory: Path | None) -> _Expanded:
    """The tree ``source`` expands to as ``module_name``: from the store under ``directory`` when
    these inputs were expanded before, else expanded here and stored there (None = no store). The
    key is made of the very source expanded, and only a tree expanded here is written."""
    if directory is None or _type_check_expansion():
        return _expand_hy(source, filename, module_name)
    place = _expansion_key(source, module_name).place(directory)
    stored = _stored_expansion(place)
    if stored is not None:
        return stored
    imported = _imported_expansion(source, module_name, directory)
    if imported is not None:
        return imported
    expanded = _expand_hy(source, filename, module_name)
    _store_expansion(place, expanded)
    return expanded


def _imported_expansion(source: str, module_name: str, directory: Path) -> _Expanded | None:
    """The tree the import side already expanded for this source (``doeff_hy_bytecode_guard.expansion_trees`` —
    a changed module is compiled by the session's import before it is analyzed, and both expand the same source
    with the same compiler: ki-e053a847c515), with the record of the macros it used; None when the import did not
    expand it here (its bytecode came from a store) or those macros are not this environment's. Read only — the
    import side is the one that writes these trees, so this module's key (``_ExpansionKey``) stays what it decides."""
    from doeff_hy_bytecode_guard.expansion_trees import read_import_tree

    imported = read_import_tree(directory, module_name, source)
    if imported is None:
        return None
    return _Expanded(tree=imported.tree, macros=imported.macros)


def _type_check_expansion() -> bool:
    """Whether expansions now are for type checking (``doeff_hy.static_view``): doeff-hy's macros
    then write another tree (no ``:pre`` check, no runtime import), which no store may keep under the
    key of a plain expansion — as the import side keeps no record of such code (``TYPE_CHECK_EXPANSION``).
    Callers keep nothing derived from such a tree either (``program_effects._hy_cache_dir``)."""
    from doeff_hy_bytecode_guard import TYPE_CHECK_EXPANSION

    return TYPE_CHECK_EXPANSION.get()


def _stored_expansion(place: Path) -> _Expanded | None:
    """The tree of the entry in ``place`` whose macros are this environment's, with their record
    (None when no entry is — the caller expands again)."""
    return next(
        (expanded for entry in sorted(place.glob("*.pickle")) if (expanded := _read_expansion(entry)) is not None),
        None,
    )


def _read_expansion(path: Path) -> _Expanded | None:
    """The entry's tree when the macros it was expanded with are this environment's; None when they
    are not, or the entry is absent or unreadable. The entry starts with the record of those macros,
    so an entry of other macros is not read past it."""
    from doeff_hy_bytecode_guard import record_is_current_here
    from doeff_hy_bytecode_guard.records import MacroRecord

    try:
        with path.open("rb") as handle:
            macros = pickle.load(handle)
            if not isinstance(macros, MacroRecord) or not record_is_current_here(macros):
                return None
            tree = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError):
        return None
    if not isinstance(tree, ast.Module):
        return None
    return _Expanded(tree=tree, macros=macros)


def _store_expansion(place: Path, expanded: _Expanded) -> None:
    """Write the entry of the macros the expansion used in ``place`` — their record, then the tree —
    atomically; a store that cannot be written only costs the next expansion."""
    try:
        place.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=place, suffix=".tmp")
        with os.fdopen(handle, "wb") as out:
            pickle.dump(expanded.macros, out, protocol=pickle.HIGHEST_PROTOCOL)
            pickle.dump(expanded.tree, out, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, place / _entry_name(expanded.macros))
    except (OSError, pickle.PicklingError, RecursionError):
        return
