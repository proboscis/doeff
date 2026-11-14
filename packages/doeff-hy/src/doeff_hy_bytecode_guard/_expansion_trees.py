"""Store for the trees that import-time compilation expanded, so doeff-effect-analyzer does not expand the same source again.

A changed Hy module is compiled at import time when each daily session gathers its checks (macro expansion is heavy: about 30 s for a 4,447-line module),
and the analyzer of the closing check expanded the same source a second time (the second run within the same second). The import compile hook (``loader_hooks``) receives, below the Hy hook,
the expanded tree and the record of the macros the expansion depended on, so those two are stored here. The analyzer looks here before its own expansion
(the direction stays analyzer -> doeff-hy; doeff-hy does not import the analyzer).

The store is a tree entry (``.tree``) inside the bytecode store (``code_store``, environment variable ``DOEFF_HY_CODE_STORE``). The compiling process
puts the tree in the same store where it puts the bytecode, so while that module's bytecode is in the store, the tree sits next to it no matter which process compiled
(previously only a compile in a process whose analyzer-store variable pointed at a directory stored a tree, and a job compile without that variable, which the worker does not pass to job children,
left no tree behind; the daily run hit the bytecode, did not compile, and the analyzer expanded again). Trees are stored and read only in a named store, where the environment variable points at a directory
(unset, empty, or ``off`` means no store, so an ordinary import compile gets no extra tree writes, and the default user cache directory is not used either).
The analyzer's own expansion-tree store (``DOEFF_EFFECT_ANALYZER_CACHE``) does not decide this location here; there are not two tree stores.

Key: the tree tag (``TREE_TAG``), the Python version, the Hy version, the record shape (``RECORD_TAG``), the module name, and the source sha256 are passed through ``code_store.content_key``;
the resulting single-key directory holds entries named by the fingerprint of the macro record the expansion used. The reader checks the record against the macros of the current environment
(``record_is_current_here``) before reading the tree, so the tree of an entry whose macros changed is never read. The path is not part of the key (a source with identical content hits
from another worktree too, as in the analyzer's store).

Entry contents: the pickle of the macro record, followed by the pickle of the tree (``ast.Module``), compressed with zlib. A tree is several times the size of the code for the same source
(about 196 KB on average; the pickle is a highly repetitive syntax tree and compresses to about 1/6 to 1/7), so it is stored compressed to keep the store at the same order of size as the bytecode
(compression costs about 2 ms per module). The cap on the number of entries (``code_store.TREE_ENTRIES_PER_KEY``) and the worker's sweep that deletes entries unused for 7 days
follow the ``code_store`` conventions: every hit refreshes the entry's timestamp to now.

Do not mix this with the analyzer's own store (``expansions/``): its keys carry the digest of the analyzer's own source, and the analyzer is the sole owner of the trees it wrote. Here, this module is the sole owner
of what the Hy compile hook received. Expansion trees for type checking are not written
(the hook records nothing for ``TYPE_CHECK_EXPANSION``, so none arrive here either).

This is not a module imported by the wrapper that is loaded at startup (it is loaded only when a Hy source is hit).
"""

import ast
import pickle
import sys
import zlib
from dataclasses import dataclass

from doeff_hy_bytecode_guard import _code_store as code_store
from doeff_hy_bytecode_guard._loader_hooks import record_fingerprint, record_is_current_here
from doeff_hy_bytecode_guard._records import RECORD_TAG, MacroRecord

#: Tag for the tree entry format (bump the trailing number when the format or location changes; old keys then stop hitting). 2 = the store lives inside the bytecode store and
#: trees are stored compressed (1 = the version stored uncompressed under the analyzer's store).
TREE_TAG = "doeff-hy/import-trees/2"

#: Compression level (zlib 1 = fastest: a syntax-tree pickle shrinks to about 1/6.5 even at level 1, and higher levels gain only about 10% — measured 2026-10-21).
COMPRESSION_LEVEL = 1


@dataclass(frozen=True)
class ImportTree:
    """The tree expanded by import-time compilation and the record of the macros that expansion depended on (the same two as the analyzer's ``_Expanded``)."""

    tree: ast.Module
    macros: MacroRecord


def tree_store() -> str | None:
    """Directory of the bytecode store that holds trees (only when the environment variable points at a directory; unset, empty, or ``off`` gives None = do not store, do not read)."""
    return code_store.store_dir() if code_store.store_is_named() else None


def _key(module_name: str, source: str | bytes) -> str:
    """Key of the tree from expanding this source as a module of this name — a digest of every part of the key, so a change in any part misses."""
    import hy  # not loaded at startup

    data = source if isinstance(source, bytes) else source.encode("utf-8")
    return code_store.content_key((TREE_TAG, sys.version, hy.__version__, RECORD_TAG, module_name), data)


def read_import_tree(module_name: str, source: str) -> ImportTree | None:
    """The tree and macro record that import-time compilation stored for this source (None if the store is not named or no entry can be used after checking against the current environment's macros).
    A hit entry has its timestamp set to now to mark it used (only when older than an hour — code_store.mark_used)."""
    store = tree_store()
    if store is None:
        return None
    for entry in code_store.tree_entries(store, _key(module_name, source)):
        found = _read_entry(entry)
        if found is not None:
            code_store.mark_used(entry)
            return found
    return None


def _read_entry(entry: str) -> ImportTree | None:
    """The tree and record of an entry (None for an entry whose record is not from the current environment's macros, or one that cannot be read). The macro record is at the start of the entry, so the tree of an entry whose record
    does not match is never read."""
    try:
        with open(entry, "rb") as handle:
            macros = pickle.load(handle)
            if not isinstance(macros, MacroRecord) or not record_is_current_here(macros):
                return None
            tree = pickle.loads(zlib.decompress(handle.read()))
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError, zlib.error):
        return None
    if not isinstance(tree, ast.Module):
        return None
    return ImportTree(tree=tree, macros=macros)


def add_import_tree(module_name: str, source: str | bytes, macros: MacroRecord, tree: object) -> None:
    """Store the tree expanded by import-time compilation together with the record of the macros used (do nothing if the store is not named, the tree is not a module, or writing fails —
    a failed write only costs the next analyzer one extra expansion. The reason for a failed write is not reported: the import hook runs at every Python startup in the venv).
    """
    store = tree_store()
    if store is None or not isinstance(tree, ast.Module):
        return
    try:
        data = pickle.dumps(macros, protocol=pickle.HIGHEST_PROTOCOL) + zlib.compress(
            pickle.dumps(tree, protocol=pickle.HIGHEST_PROTOCOL), COMPRESSION_LEVEL
        )
    except (pickle.PicklingError, RecursionError):
        return
    code_store.add_tree(store, _key(module_name, source), record_fingerprint(macros), data)
