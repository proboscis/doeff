"""Storage for bytecode looked up by source content — this one module is the entry point for lookup, adding, the used mark, and handling of broken entries (
#2799, #3858).

There are 6 users, and all of them go only through here (no duplicate copies of the storage layout or the key spelling):
  - Compiling the source of the per-type functions that dataclass generates (:mod:`doeff_hy_bytecode_guard._dataclass_code`): look up code by the statement contents,
    and add it after compiling.
  - The import loader hook (:mod:`doeff_hy_bytecode_guard._loader_hooks`): for a Hy module whose worktree .pyc cannot be used, look up the code by checking the record of the entries lined up under the key
    (:func:`current_code`), and after compiling add it as an entry for the current environment (:func:`add_code`).
  - Reading the source for the .pyc record check (:mod:`doeff_hy_bytecode_guard._bound_source`): the walk computing the macro closure digest looks up the reading of the source statements
    that cover globals it cannot trace (a pure reading determined by content and name), and resolves and adds it if absent (a new
    process had been re-resolving the same source content every time).
  - Reading the code for the .pyc record check (:mod:`doeff_hy_bytecode_guard._macro_use`): the readings of the function code that the macro closure digest walk reads
    (name tables, string constants, imports, the digest shape — pure readings determined by the code content), lined up in one entry per walk step and looked up; when re-read,
    they are added.
  - Trees expanded by import-time compilation (:mod:`doeff_hy_bytecode_guard._expansion_trees`): the process that compiled a changed Hy module adds the expanded
    tree and the record of the macros the expansion depended on next to its code (:func:`add_tree`). Before doeff-effect-analyzer expands the same source again, it checks against the current
    environment's macros and looks them up (:func:`tree_entries`). The bytecode store is shared by all jobs on a worker, so whichever job compiled,
    the analyzer that reads later (the daily closing check) can look it up from the same store.
  - The bytecode tools of the doeff-cluster worker (worker/entry/code_prepare.hy and _foundation/bytecode_pool.hy): across versions, look up the code of sources with the same content
    (:func:`current_code`, :func:`add_code`) and import names, compile only what is missing, and add it. The tools run under the python of the root venv being prepared, so they read
    this file of the worker's version by path, not the root's doeff-hy — hence only the standard library is imported (no other module of this package is read either).

Store directory = environment variable DOEFF_HY_CODE_STORE (``off`` = do not use; if unset, ``$XDG_CACHE_HOME/doeff-hy/code-store``). The worker's startup
script puts the default at ``$WORK_DIR/state/doeff-hy-code-store`` (the same directory as the daily full-verification task).

Layout: ``<dir>/<first 2 chars of key>/<rest of key><kind suffix>``. Kinds = ``.code`` (marshaled code object — the code of dataclass functions) and
``.imports`` (import names read by the closure walk of the worker tools — the shape is owned by the tools), ``.binding`` and ``.facts`` (the source reading and the code reading for the
.pyc record check — shapes owned by bound_source and macro_use), and ``.tree`` (the tree expanded by import-time compilation and the macro record — shape owned by expansion_trees).
The key is determined only by the sha256 of the source content and the things that change how the content is read (the fields of :func:`code_key`),
and does not depend on path or version (commit). Only ``.pyc`` (the exact contents of the .pyc the worker tools place in a tree) is keyed by the sha256 of the content, and the tree's
.pyc is a hardlink to this file (:func:`link_pyc`).

For source code (the key of :func:`code_key` — the import hook and the worker tools), the single-key directory holds several entries, one per macro environment:
``<dir>/<first 2 chars of key>/<rest of key>/<record fingerprint>.code``. A Hy source carries in its code the record of the macros its expansion depended on, and
the reader checks it against the current environment's macros (the check is the caller's job — :func:`current_code`). The record fingerprint (:func:`code_fingerprint`) covers everything that decides
whether it can be used, so code with the same record rewrites one entry, while code with different records (imports from worktrees of different doeff versions, and runtime-environment preparation) sit side by side.
With one entry per key (key tag 2), writers of different versions saw each other's entry under the same key as stale, recompiled, and overwrote each other (2026-10-12
zeus — runtime-environment preparation rebuilt 100 to 600 files at a time even for sources of the same version). A key holds at most :data:`CODE_ENTRIES_PER_KEY` entries, and
past that the oldest by timestamp are removed. A just-added entry is placed at a timestamp older than the key's other entries (:func:`add_code`) — until someone checks and uses it, it stays
at the back of the order and is removed first when the cap is exceeded. So entries of one-off environments (checks in a worktree where macros are being fixed) evict each other and
do not push out the entries of writers that keep using them. Even when more environments than the cap use the same key in turn, only the excess falls out (it is not a cliff where everything falls
out).

Trees (``.tree``) follow the same layout — a single-key directory holds an entry per macro environment (name = record fingerprint), an added entry goes to the back, and the cap is
:data:`TREE_ENTRIES_PER_KEY`. A tree is several times the size of the code for the same source (measured 2026-10-21, modules compiled during imports of the screen job:
634 expansion-tree pickles averaged about 196 KB, 1,306 code objects averaged about 30 KB), so the writer (expansion_trees) adds them compressed. The key is determined by the tree-side tag, the module name, and the source content, and lines up in a
different directory from the code keys (the two do not share a directory).

Used mark: every hit refreshes the entry's timestamp to now (for source code, only the entry that is checked and used). The worker's sweep (env_store's
sweep-leftovers) removes entries unused for 7 days and directories whose contents have all been idle for 7 days — the same 7-day convention as the native wheel store (a .used mark
advances the directory's timestamp). Writes go to a temporary file in the same directory and are then swapped in (so they do not mix with another process writing at the same time, and a half-written file is never read).

A broken entry (unreadable as code, or import names in the wrong shape) is not used: one line naming it goes to stderr and the entry is removed, and the caller rebuilds and
adds it again (a broken entry is neither used silently nor discarded silently).
"""

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import CodeType

#: Environment variable pointing at the store directory (value = directory path; ``off`` = do not use).
STORE_ENV = "DOEFF_HY_CODE_STORE"

#: Tag for the code entry key (bump the trailing number when the format changes — entries under old keys stop hitting). 2 = a version that builds the key from the compiled bytes
#: themselves. The store of version 1 may hold entries where code with old contents sits under a key made by re-reading the file after compilation,
#: so the newer versions do not look them up. 3 = a version that lines up one entry per macro environment in the single-key directory. Version 2 entries (one
#: file per key) are used only by writers of the previous version and the new version does not look them up — so they do not compete with previous-version writers, and the sweep removes them after 7 unused days.
CODE_TAG = "doeff-hy/code-store/3"

#: Cap on the number of entries held per source-code key (when adding goes over it, the oldest by timestamp are removed). The cap is the number of "entries that are checked and kept in use"
#: that are protected at once — an added but not yet used entry lines up at the back (:func:`add_code`), so it does not depend on the number of one-off environments.
#: If the environments in continued use exceed the cap, the excess environments fall out every time (not all of them). Too many, and a new environment that matches no entry
#: costs more effort checking all of them before compiling.
CODE_ENTRIES_PER_KEY = 16

#: Lower bound for placing a just-added entry's timestamp ahead of the key's other entries (never place it further back than this from now). Much shorter than the sweep's 7 days — so that an added
#: entry that was never checked and used is not placed at a timestamp past the sweep threshold and deleted immediately.
NEW_ENTRY_FLOOR_SECONDS = 24 * 3600

#: Cap on the number of entries held per key for trees expanded by import-time compilation (when adding goes over it, the oldest by timestamp are removed — removed the same way as code entries).
#: A tree is several times the size of the code for the same source (about as large as the code even when stored compressed), and its user does one lookup to avoid re-expanding the same source, so
#: few macro environments stay in continued use (the verification jobs, the daily run, and a local checkout) — make it smaller than the code cap.
TREE_ENTRIES_PER_KEY = 4

#: Suffix for the entry kind.
CODE_SUFFIX = ".code"
#: The tree expanded by import-time compilation and the record of the macros that expansion depended on (doeff_hy_bytecode_guard._expansion_trees — it owns the content shape).
TREE_SUFFIX = ".tree"
IMPORTS_SUFFIX = ".imports"
#: Reading of the source statements covering globals the macro closure digest walk cannot trace (doeff_hy_bytecode_guard._bound_source — a pure reading determined by
#: content and name. Its shape is owned by bound_source).
BINDING_SUFFIX = ".binding"
#: Reading of the code object that the macro closure digest walk reads (doeff_hy_bytecode_guard._macro_use — a pure reading determined by the code content. Its shape is
#: owned by macro_use).
FACTS_SUFFIX = ".facts"
#: The exact contents of the .pyc that the worker tools place in a tree (header plus marshaled code — named by the content's sha256; :func:`link_pyc`).
PYC_SUFFIX = ".pyc"

#: Extension of Hy sources (doeff-hy also has .hyk and .hyp read as Hy) — decides whether the module name and Hy version go into the code key.
HY_SOURCE_SUFFIXES: tuple[str, ...] = (".hy", ".hyk", ".hyp")


@dataclass(frozen=True)
class StoredCode:
    """Entry of the source code that was looked up: code = the code object read, data = the bytes of the entry content (marshaled code — so it can be placed after the .pyc header without re-reading;
    :func:`current_entry`)."""

    code: CodeType
    data: bytes


def store_dir() -> str | None:
    """Store directory (None if configured not to use it) — the environment variable STORE_ENV, or else under the user's cache directory."""
    configured = os.environ.get(STORE_ENV, "").strip()
    if configured == "off":
        return None
    if configured:
        return configured
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "doeff-hy", "code-store")


def store_is_named() -> bool:
    """Whether the store directory is named by the environment variable STORE_ENV (``off`` and empty are not named) — a named store is added to even
    when configured not to write .pyc into the tree (the no-write setting is a decision about writing to the tree, not about a store outside the tree). An unnamed default directory
    (the user's cache) is not added to under the no-write setting."""
    configured = os.environ.get(STORE_ENV, "").strip()
    return configured not in ("", "off")


def content_key(parts: tuple[str, ...], source: bytes) -> str:
    """Entry key — the list parts of things that change how the content is read (the first is the tag of kind and shape version) and the sha256 (hex) of the source content. Each of parts is
    separated by NUL (so shifting the boundary between adjacent fields never yields the same key)."""
    import hashlib  # loaded only when the store is used

    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    digest.update(source)
    return digest.hexdigest()


def code_key(path: str, source: bytes, module_name: str, hy_version: str, cache_tag: str, optimize: int) -> str:
    """Code key for one source. A Hy source also includes the module name (to resolve relative requires) and the Hy version (expansion). Compiling a Python source
    depends on neither, so it uses only the content, the Python version tag (cache_tag), and the optimization level. Macro dependencies are not in the key; they are checked against the record of the entry that
    hit (dependencies are unknown before compiling)."""
    hy = path.endswith(HY_SOURCE_SUFFIXES)
    parts = (CODE_TAG, module_name if hy else "", hy_version if hy else "", cache_tag, str(optimize))
    return content_key(parts, source)


def entry_path(store: str, key: str, suffix: str) -> str:
    """Path of the key's entry (the first 2 chars of the key split the directories)."""
    return os.path.join(store, key[:2], key[2:] + suffix)


def _plain(value: object) -> bool:
    """Whether value is a value of the record shape — strings, bytes, integers, floats, None, and nested tuples of those (values whose repr does not depend on the process or the tree —
    values whose order depends on hashing, like frozenset, are excluded). If a record field later leaves this shape in a newer version, code with different records collapses into one fixed name and
    they overwrite each other, so fields added to the record must be written in this shape."""
    match value:
        case str() | bytes() | int() | float() | None:
            return True
        case tuple():
            return all(_plain(item) for item in value)
        case _:
            return False


def code_fingerprint(code: CodeType) -> str:
    """Name of a source-code entry — the first 32 chars of the sha256 of the tail of the code's constants (where doeff-hy puts the macro record — :mod:`doeff_hy_bytecode_guard._records`).
    The record covers everything that decides whether the entry can be used (Hy version, macros used and digests, ...), so code with the same record gets the same name
    (the path of the compiled tree is not in the record) and code with a different record gets a different name. The meaning of the record fields is not read (any value of the :func:`_plain` shape is built the same way) —
    the worker tools name the root-version record with this file of the worker version, so the same naming works for records of versions with a different record tag (RECORD_TAG).
    Code whose tail constant is not of that shape (code with no record — Python sources) gets one fixed name."""
    import hashlib  # loaded only when adding to the store

    last = code.co_consts[-1] if code.co_consts else None
    text = repr(last) if isinstance(last, tuple) and _plain(last) else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _code_dir(store: str, key: str) -> str:
    """Directory holding the entries of one source-code key (the first 2 chars of the key split the directories — the same as the other kinds)."""
    return os.path.join(store, key[:2], key[2:])


def _used_at(item: os.DirEntry[str]) -> int | None:
    """Used time (ns) of an entry — None for an entry that another process removed after the listing (it no longer exists)."""
    try:
        return item.stat(follow_symlinks=False).st_mtime_ns
    except FileNotFoundError:
        return None


def _stamped_entries(store: str, key: str, suffix: str = CODE_SUFFIX) -> tuple[tuple[int, str], ...]:
    """List of (time ns, path) for the entries of key (kind suffix — code or tree) — newest time first (ties in name order). Empty if the key's directory does not exist.
    Half-written temporary files (names not ending in suffix) are not counted. For an unreadable directory, one line naming it is printed and the result is empty (the caller rebuilds)."""
    directory = _code_dir(store, key)
    try:
        with os.scandir(directory) as listing:
            stamped = [(_used_at(item), item.path) for item in listing if item.name.endswith(suffix)]
    except FileNotFoundError:
        return ()
    except OSError as error:
        print(f"doeff-hy: cannot read the bytecode store's key directory ({error}) — not using {directory}; rebuilding", file=sys.stderr, flush=True)
        return ()
    return tuple(sorted(((at, path) for at, path in stamped if at is not None), key=lambda pair: (-pair[0], pair[1])))


def code_entries(store: str, key: str) -> tuple[str, ...]:
    """List of entry paths for the source-code key key — newest time first (entries checked and used come first, merely added entries come last — readers
    check in this order). Empty if the key's directory does not exist."""
    return tuple(path for _, path in _stamped_entries(store, key))


def tree_entries(store: str, key: str) -> tuple[str, ...]:
    """List of entry paths for the key key of a tree expanded by import-time compilation — the same order as :func:`code_entries` (used entries first, merely added entries
    last). The tree key and the content shape are owned by doeff_hy_bytecode_guard._expansion_trees, and readers check the macro record in this order and use it."""
    return tuple(path for _, path in _stamped_entries(store, key, TREE_SUFFIX))


def current_code(store: str, key: str, is_current: Callable[[CodeType], bool]) -> CodeType | None:
    """The first of the source-code entries of key key for which is_current (checking the record against the current environment's macros — owned by the caller) is true (checked
    in newest-used-time order; None if there is none). The used mark goes only on the entry that is used — the time of an entry that was checked and did not match is not advanced (so the order of removal at the count cap and the
    sweep's 7 days are decided only by uses of writers that use that entry). A broken entry is named and removed by :func:`stored_code`, and the next one is checked."""
    found = current_entry(store, key, is_current)
    return None if found is None else found.code


def current_entry(store: str, key: str, is_current: Callable[[CodeType], bool]) -> StoredCode | None:
    """The code and content bytes (marshaled code) of the entry chosen by the same check as :func:`current_code` — so the worker tools can place the content as-is after the .pyc header, and the .pyc placed from the same entry across roots has identical bytes
    (re-marshaling can change the bytes depending on in-process reference counts,
    and :func:`link_pyc` could not share them)."""
    for path in code_entries(store, key):
        found = _stored_entry(path, touch=False)
        if found is not None and is_current(found.code):
            touch_entry(path)
            return found
    return None


def add_code(store: str, key: str, code: CodeType, data: bytes | None = None) -> str | None:
    """Add code to the entry of source-code key key (name = :func:`code_fingerprint` — an entry with the same record is rewritten). The added entry's time is
    placed before every other entry of the key (but never further back than :data:`NEW_ENTRY_FLOOR_SECONDS` from now) and moved forward only when it is checked and used.
    If after adding, the key's entries exceed :data:`CODE_ENTRIES_PER_KEY`, the oldest by time are removed, keeping the entry just added — what gets removed is
    entries that were only added and never used, and entries that have not been used for a long time. data = the marshaled bytes of code (when the caller already has them — the bytes after the header of a baked
    .pyc are made the entry as-is, so the .pyc next placed from the entry has the same bytes as the .pyc just placed; :func:`link_pyc`. If absent,
    marshal it). Returns the reason it could not be written (same as :func:`write_entry` — None if it was written)."""
    import marshal  # loaded only when adding to the store

    return _add_entry(
        store, key, code_fingerprint(code), CODE_SUFFIX, CODE_ENTRIES_PER_KEY, marshal.dumps(code) if data is None else data
    )


def add_tree(store: str, key: str, name: str, data: bytes) -> str | None:
    """Add a tree entry expanded by import-time compilation to key key (name = name — the fingerprint of the macro record the expansion depended on; an entry with the same name is rewritten).
    How the added entry's time is placed and how the cap on the key's entry count (:data:`TREE_ENTRIES_PER_KEY`) is kept are the same as :func:`add_code`. Returns the reason it could not be
    written (same as :func:`write_entry` — None if it was written)."""
    return _add_entry(store, key, name, TREE_SUFFIX, TREE_ENTRIES_PER_KEY, data)


def _add_entry(store: str, key: str, name: str, suffix: str, limit: int, data: bytes) -> str | None:
    """Add an entry of key key (kind suffix) under name — place the added entry at the back of the order and remove whatever exceeds the per-kind cap limit (:func:`add_code`)."""
    path = os.path.join(_code_dir(store, key), name + suffix)
    problem = write_entry(path, data)
    if problem is not None:
        return problem
    others = tuple(pair for pair in _stamped_entries(store, key, suffix) if pair[1] != path)
    _place_after(path, others)
    for _, stale in others[limit - 1 :]:
        try:
            os.unlink(stale)
        except FileNotFoundError:
            pass  # if another process removed it concurrently, there is nothing to delete
    return None


def _place_after(path: str, others: tuple[tuple[int, str], ...]) -> None:
    """Place the added entry's time 1 second before every other entry of the key (others — newest time first) (but never further back than NEW_ENTRY_FLOOR_SECONDS from now).
    If it cannot be placed, one line naming it is printed (the entry is still usable — it is just at the head of the order)."""
    import time  # loaded only when adding to the store

    now = time.time_ns()
    oldest = others[-1][0] if others else now
    at = max(now - NEW_ENTRY_FLOOR_SECONDS * 1_000_000_000, oldest - 1_000_000_000)
    try:
        os.utime(path, ns=(at, at))
    except OSError as error:
        print(f"doeff-hy: cannot set the time of an entry added to the bytecode store ({error}) — {path}", file=sys.stderr, flush=True)


def touch_entry(path: str) -> None:
    """Set the entry's time to now (the used mark — so the sweep does not remove an entry in use). If it cannot be advanced, one line naming it is printed (the content is still usable)."""
    try:
        os.utime(path)
    except OSError as error:
        # The content read is usable. If the time cannot be advanced, the sweep will delete this entry after 7 days (it will just be rebuilt when next needed).
        print(f"doeff-hy: cannot advance the time of an entry in the bytecode store ({error}) — {path}", file=sys.stderr, flush=True)


def read_entry(path: str, *, touch: bool = True) -> bytes | None:
    """Read an entry's content (None if absent). For an entry unreadable for a reason other than absence, one line naming it is printed and None is returned (the caller rebuilds). On a hit,
    the entry's time is set to now (not advanced if touch is false — a caller that checks before using advances it with :func:`touch_entry`)."""
    try:
        with open(path, "rb") as entry:
            data = entry.read()
    except FileNotFoundError:
        return None
    except OSError as error:
        print(f"doeff-hy: cannot read an entry in the bytecode store ({error}) — not using {path}; rebuilding", file=sys.stderr, flush=True)
        return None
    if touch:
        touch_entry(path)
    return data


def write_entry(path: str, data: bytes) -> str | None:
    """Write an entry (to a temporary file in the same directory, then swap it in). Returns the reason it could not be written (None if written) — the store exists only for speed,
    so the caller's result does not change, but the caller prints the reason naming it (a store that cannot be written is not silently kept in use)."""
    import tempfile  # loaded only when writing to the store

    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    except OSError as error:
        return f"cannot write to {directory}: {error}"
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass  # if it vanished before the swap, there is nothing to delete
        return f"cannot place {path}: {error}"
    return None


def discard_entry(path: str, problem: str) -> None:
    """Remove a broken entry with one line naming it (the caller rebuilds and adds it again)."""
    print(f"doeff-hy: an entry in the bytecode store is broken ({problem}) — removing {path} and rebuilding", file=sys.stderr, flush=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass  # if another process removed it concurrently, there is nothing to delete


def stored_code(path: str, *, touch: bool = True) -> CodeType | None:
    """Look up a code entry (None if absent). An entry unreadable as code is removed with discard_entry and gives None. Checking the macro record is the caller's job
    (only for Hy sources — checked against the current environment's macro files). touch has the same meaning as in :func:`read_entry`."""
    found = _stored_entry(path, touch=touch)
    return None if found is None else found.code


def _stored_entry(path: str, *, touch: bool) -> StoredCode | None:
    """Look up the code and content bytes of a code entry (the read behind :func:`stored_code` and :func:`current_entry` — broken entries are handled the same way)."""
    data = read_entry(path, touch=touch)
    if data is None:
        return None
    import marshal  # loaded only on a hit

    try:
        code = marshal.loads(data)
    except (EOFError, ValueError, TypeError) as error:
        discard_entry(path, f"cannot be read by marshal: {error}")
        return None
    if not isinstance(code, CodeType):
        discard_entry(path, f"not a code object: {type(code).__name__}")
        return None
    return StoredCode(code, data)


def link_pyc(store: str, pyc: bytes, target: str) -> str | None:
    """Place a tree's .pyc (target) as a hardlink to the file in the store with the same content (kind :data:`PYC_SUFFIX`, named by the content's sha256) — writing it
    to the store first if absent. Writing a fresh .pyc with the same content for every root made macOS local snapshots keep that content for 24 hours even after the root was deleted,
    and the free space on the work disk shrank by the number of root rebuilds. Returns the reason it could not be placed (the caller writes the content to target
    as before — a different file system, a link-count limit, etc.). None if placed.

    The hardlink target is never overwritten in place: the store's file is made by swapping in from a temporary file and is read-only. The hands that rewrite a tree's .pyc (the worker's
    baking tool, the import hook, and Python's import) all write to a temporary file and then swap it in (they never open and write in place), so other trees' .pyc and the
    store's content do not change. The used mark is the same as for .code (the time is set to now on every use — the sweep removes what is unused for 7 days. The tree's name remains)."""
    import errno  # loaded only when placing from the store
    import hashlib

    path = entry_path(store, hashlib.sha256(pyc).hexdigest(), PYC_SUFFIX)
    try:
        seen = os.stat(path)
    except FileNotFoundError:
        seen = None
    except OSError as error:
        return f"cannot read the .pyc in the store: {errno.errorcode.get(error.errno, error.errno)} {error.strerror}"
    if seen is not None and seen.st_size == len(pyc):
        touch_entry(path)
    else:
        problem = write_entry(path, pyc)
        if problem is not None:
            return problem
        seen = None
    if seen is None or seen.st_mode & 0o222:  # also align, at use time, a file from a writer that died before making it read-only (refuse writing hands)
        try:
            os.chmod(path, 0o444)
        except OSError as error:
            return f"cannot make the .pyc in the store read-only: {errno.errorcode.get(error.errno, error.errno)} {error.strerror}"
    try:
        # If the tree's .pyc already has this file's name, do not place it again (a rename onto the same inode does nothing and leaves the temporary link behind).
        if os.path.samestat(os.lstat(target), os.stat(path)):
            return None
    except (FileNotFoundError, NotADirectoryError):
        pass  # the tree has no .pyc yet — place it below (the link below answers with the reason if it cannot be placed)
    except OSError as error:
        return f"cannot read the tree's .pyc: {errno.errorcode.get(error.errno, error.errno)} {error.strerror}"
    temporary = f"{target}.{os.getpid()}.link"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.link(path, temporary)
        os.replace(temporary, target)
    except OSError as error:
        if os.path.lexists(temporary):  # the created link remains only when the swap was refused after the link was made
            os.unlink(temporary)
        return f"cannot place the store's .pyc into the tree as a hardlink: {errno.errorcode.get(error.errno, error.errno)} {error.strerror}"
    return None


def entry_pyc(head: bytes, entry: StoredCode) -> bytes:
    """Build the .pyc bytes to place in a tree from a looked-up entry — the entry content is placed as-is after the header (16 bytes in the caller's style — checked hash or timestamp)
    (no re-marshaling). The same entry and the same header give the same bytes across roots, and :func:`link_pyc` shares one file in the store. Only this function knows how to build it — the baking tool and
    any other writer that brings a .pyc over call this function and :func:`link_pyc` (
    when the build lived only in the baking tool, a second writer re-marshaled and could not share)."""
    return head + entry.data


def link_entry_pyc(store: str, head: bytes, entry: StoredCode, target: str) -> str | None:
    """Place the .pyc of a looked-up entry (:func:`entry_pyc`) at target as a hardlink to the file in the store with the same content. The answer is the same as :func:`link_pyc`'s
    (the reason it could not be placed — the caller writes the bytes of :func:`entry_pyc`)."""
    return link_pyc(store, entry_pyc(head, entry), target)
