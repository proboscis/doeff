"""Wrappers for the two entry points of ``SourceFileLoader`` (this module is the only one that reads or writes files).

It is loaded at startup, so imports are limited to light standard-library modules. The record shape and the matching decisions live in
:mod:`doeff_hy_bytecode_guard._records` (pure functions), which is imported when a Hy source is first encountered.

It works whether other wrappers (Hy's ``source_to_code``, doeff-hy-pytest timing) are layered outside or inside these:
each wrapper just calls the previous function at call time, and this module's wrappers only look at "is it Hy source?" and "right after compile?".
"""

import importlib.machinery
import importlib.util
import io
import os
import sys
from collections.abc import Callable, Iterable
from types import CodeType, ModuleType

# Marker for expansion done for type checking (a light module that reads only contextvars -- fine to load at startup. If first loaded in the middle of a compile, that .pyc
# would be written inside a configuration that writes bytecode).
from doeff_hy_bytecode_guard._expansion import TYPE_CHECK_EXPANSION

# Memory kept for the duration of one outermost load (a light module that reads only contextvars and _thread -- fine to load at startup).
from doeff_hy_bytecode_guard._import_scope import IMPORT_SCOPE, ImportScope, current_scope

# Owner of the GC threshold for the duration of the outermost load (a light module that reads only gc, os and _thread -- fine to load at startup).
from doeff_hy_bytecode_guard import _import_gc as import_gc

TYPE_CHECKING = False  # do not load typing at startup (2 ms) -- type checkers read the branch on this name as true

if TYPE_CHECKING:
    import contextlib

    from doeff_hy_bytecode_guard._macro_use import MacroRecording
    from doeff_hy_bytecode_guard._records import MacroRecord, ValueReference

#: Extensions of Hy source (doeff-hy also has .hyk and .hyp read as Hy -- doeff_hy/__init__.py). The definition site is the store module (the code key
#: splits fields by whether it is Hy source) -- a light module that reads only the standard library, so fine to load at startup.
from doeff_hy_bytecode_guard._code_store import HY_SOURCE_SUFFIXES as HY_SOURCE_SUFFIXES

GetCode = Callable[[importlib.machinery.SourceFileLoader, str], "CodeType | None"]
GetData = Callable[[importlib.machinery.SourceFileLoader, str], bytes]
SourceToCode = Callable[..., CodeType]
ExecModule = Callable[[importlib.machinery.SourceFileLoader, ModuleType], None]

#: Whether the wrap has been done (one per process). The wrap rewrites one shared object, the process's ``SourceFileLoader``, so
#: this module is the sole owner.
#: Called only from .pth (one thread at startup) and ``import doeff_hy`` (inside the import lock), so it holds no lock.
_installation = {"installed": False, "dataclasses": False}

#: Number of times Hy source was compiled in this thread (per path) -- so get_code can tell "did I compile within this call?"
#: from the difference between the counts before and after (get_code nests -- a require inside a compile imports another module).
#: threading is not loaded at startup, so this is created when Hy source is first encountered.
_compile_counts: dict[str, object] = {}

#: Bytes of the Hy source last read in this thread (per path) -- so the compiled bytes can be used for the store key.
#: Discarded for that path when get_code finishes (the source is not held for the life of the process).
_source_reads: dict[str, object] = {}

#: Contents of the .pyc of a Hy module that the load entry read for its header, kept per thread until the standard get_code reads the same file
#: (:func:`_read_pycs`). threading is not loaded at startup, so this is created when Hy source is first encountered.
_pyc_reads: dict[str, object] = {}

#: sha256 of a file -- key is (path, mtime ns, size). The same macro file is checked hundreds of times in one process, so
#: unchanged files are not re-read.
_digests: dict[tuple[str, int, int], str] = {}

#: State of the source file whose body was executed (path -> (mtime ns, size) -- what the load entry read just before executing the body; lives for the process).
#: The step that checks .pyc records reads, from inside the process, the values of loaded modules, and from the current disk the statements covering those values and the files' sha256.
#: In a resident process where a file was rewritten after the module ran, the two disagree, so the step verifies this.
_executed: dict[str, tuple[int, int]] = {}

#: Records attached to the code of Hy modules returned by the load entry (source path -> record; code without a record is not stored; lives for the process).
#: So that the record cache for Hy test-file items (doeff-adr item_cache) can look up the macros used in the expansion of an imported test file
#: without re-reading the file (:func:`loaded_record`).
_loaded_records: dict[str, "MacroRecord"] = {}

#: The code whose record was read last and its record (key "entry" -> (code, record or None)) -- so :func:`_record_of` does not rebuild the record of the same code.
_last_record: dict[str, "tuple[CodeType, MacroRecord | None]"] = {}


def installed() -> bool:
    """Whether this process's ``SourceFileLoader`` is wrapped (so the check can confirm the startup .pth took effect)."""
    return _installation["installed"]


def install() -> None:
    """Wrap the two entry points of ``SourceFileLoader`` so Hy modules whose macros changed are recompiled (only once however many times it is called).
    Also wraps the module-body execution entry and sets up a memory (:mod:`doeff_hy_bytecode_guard._import_scope`) so that during the outermost execution the check reads file state only once.
    If dataclasses is already loaded, it also memoizes the compile of per-type function sources (:mod:`doeff_hy_bytecode_guard._dataclass_code`)."""
    _remember_dataclass_code()
    if _installation["installed"]:
        return
    loader = importlib.machinery.SourceFileLoader
    loader.source_to_code = _recording_source_to_code(loader.source_to_code)
    loader.get_code = _checking_get_code(loader.get_code)
    loader.get_data = _remembering_get_data(loader.get_data)
    loader.exec_module = _scoped_exec_module(loader.exec_module)
    _installation["installed"] = True


def _remember_dataclass_code() -> None:
    """Make the compile of loaded dataclasses function sources memoized (only once). It does not load dataclasses itself -- it is usually not yet present at the startup
    .pth and is loaded by the Hy import, so it is called again when Hy source is first read (at the load entry)."""
    if _installation["dataclasses"]:
        return
    dataclasses = sys.modules.get("dataclasses")
    if dataclasses is None:
        return
    from doeff_hy_bytecode_guard import _dataclass_code as dataclass_code  # load only when dataclasses is present

    dataclass_code.install(dataclasses)
    _installation["dataclasses"] = True


def is_hy_source(path: object) -> bool:
    """Whether this is a source the wrapper handles (only paths whose string has a Hy extension -- anything else is passed straight to the previous function)."""
    return isinstance(path, str) and path.endswith(HY_SOURCE_SUFFIXES)


def loaded_record(path: str) -> "MacroRecord | None":
    """The record attached to the code of a Hy module returned by the load entry in this process (path = source file. None if not loaded or the code has no record)
    -- the public entry so that the record cache for Hy test-file items (doeff-adr item_cache) can look up the macros used in the expansion of an imported test file
    without re-expanding or re-reading the file. The check is :func:`record_is_current_here` (the same single check as for .pyc
    -- it covers only the contents of functions run during expansion. Checking per file made any line change in a macro provider chill every
    item record; child of #3769)."""
    return _loaded_records.get(path)


def macro_recording(module: ModuleType) -> "MacroRecording":
    """The context around the expansion (compile) of a Hy module -- collects the macros used by the expansion inside ``with macro_recording(module) as recording:``, and
    ``recording.record(source)`` builds the record. The public entry so that the record the compile entry adds to code (.pyc), the cache of expanded trees (doeff-effect-analyzer),
    and the type-check expansion store (doeff_hy._static_cache) all use
    the same single way of collecting and building. ``also`` of ``record`` is where to find values that change the expansion result outside macros (post-processing of type-check expansion)."""
    from doeff_hy_bytecode_guard import _macro_use as macro_use  # not loaded at startup

    return macro_use.MacroRecording(module, _hy_version(), file_sha256)


def gensym_renaming(names: "Iterable[str]") -> "Callable[[str], str]":
    """A function that renumbers the Hy gensym names appearing in a sequence of names from 1 in ascending order of their count (other names are returned as is) -- the same rule as canonicalizing a code tree
    (``records.canonical_gensyms``), applied also to the type-check expansion tree (doeff_hy._static_check). The
    public entry. The expansion text no longer depends on what was expanded earlier in the same process or in which process it was expanded."""
    from doeff_hy_bytecode_guard import _records as records  # not loaded at startup

    renames = records.gensym_renames(names)
    return lambda name: records.renamed(name, renames)


def record_is_current_here(record: "MacroRecord") -> bool:
    """Whether the record matches the current Hy version and the macros of the current environment -- look up the used macros again in the current environment by module name (import the defining
    module -- if it cannot be looked up, it is stale) and compare the digest of that code's closure. For rows where the record or the current step hits a module that is in the middle of importing,
    compare the sha256 of the files of the modules the closure depended on, without importing, instead of the digest. The record has no file paths, so a .pyc built in another tree (one the runtime environment
    preparation carries over from the previous root by hardlink) or code in the store is also checked against the macros of the current tree. The public entry so that the .pyc record,
    the cache of expanded trees (doeff-effect-analyzer), and the type-check expansion store (doeff_hy._static_cache) all use the same single
    way of checking."""
    from doeff_hy_bytecode_guard import _macro_use as macro_use  # not loaded at startup

    return macro_use.record_is_current(record, _hy_version(), file_sha256)


def value_reference(module: str, name: str) -> "ValueReference":
    """Where to find values that change the expansion result outside macros (module name and attribute name) -- passed to ``also`` of ``recording.record``. The public entry so that the
    owner of the record shape (records) is not imported directly."""
    from doeff_hy_bytecode_guard import _records as records  # not loaded at startup

    return records.ValueReference(module, name)


def record_fingerprint(record: "MacroRecord") -> str:
    """Fingerprint of the record contents -- the public entry so that the entry names of the cache of expanded trees (doeff-effect-analyzer) and the type-check expansion store (doeff_hy._static_cache)
    use the same single way of building."""
    from doeff_hy_bytecode_guard import _records as records  # not loaded at startup

    return records.record_fingerprint(record)


def record_to_json(record: "MacroRecord") -> list[object]:
    """Turn a record into a JSON-writable value -- the public entry so that the store that writes records to a file and reads them back (type-check expansion store doeff_hy._static_cache) does not import the owner of
    the record shape (records) directly."""
    from doeff_hy_bytecode_guard import _records as records  # not loaded at startup

    return records.record_to_json(record)


def record_from_json(value: object) -> "MacroRecord | None":
    """Build a record from a value read from JSON (None if the shape is wrong) -- the inverse of :func:`record_to_json`."""
    from doeff_hy_bytecode_guard import _records as records  # not loaded at startup

    return records.record_from_json(value)


def source_to_code_as_import(
    loader: importlib.machinery.SourceFileLoader, data: bytes, path: str
) -> CodeType:
    """Entry that compiles source outside an import (used by the tool that precompiles bytecode) -- Hy source is compiled, as in an import, with its module placed in
    ``sys.modules``, and the record of the macros the expansion depended on is added to the code.

    Calling ``source_to_code`` directly outside an import makes Hy create a temporary module and delete it right after compile, so the compile-entry wrapper cannot
    see the module and adds no record. A .pyc without a record is recompiled at import as possibly stale, so the bytecode
    built in advance was wasted and all macro expansion was redone at startup (about 43 of the roughly 46 seconds of CPU
    at startup of a store job). Non-Hy source calls ``source_to_code`` as is."""
    install()
    if not is_hy_source(path):
        return loader.source_to_code(data, path)
    present = sys.modules.get(loader.name)
    if present is not None and vars(present).get("__file__") == path:
        with macro_recording(_expansion_or(loader, path, present)):  # compile inside the loaded module (same as in the middle of an import)
            return loader.source_to_code(data, path)
    with _PlacedExpansion(loader, path):  # the compile-entry wrapper adds the record of the macros this expansion used
        return loader.source_to_code(data, path)


class _PlacedExpansion:
    """A context that collects the macros used by expansion with the module placed in ``sys.modules`` (restores the previous state on exit) -- in a compile with no module to place,
    if Hy creates a temporary module and deletes it right after compile, the compile-entry wrapper cannot see the module and adds no record.

    Used in two places: :func:`source_to_code_as_import` (the tool that precompiles bytecode outside an import), and loads where the load entry is called without going through an import
    (runpy's ``hy <script>`` and ``hy -m`` -- :func:`_expansion_recording`). In the latter, the .pyc got no record, so the next load
    always recompiled it as stale and it never entered the store."""

    def __init__(self, loader: importlib.machinery.SourceFileLoader, path: str) -> None:
        self._loader = loader
        self._path = path
        self._present = sys.modules.get(loader.name)
        self._mut_recording: MacroRecording | None = None  # annotations inside functions are not evaluated at runtime (MacroRecording is imported only for type checking)

    def __enter__(self) -> "MacroRecording":
        loader, path = self._loader, self._path
        spec = importlib.util.spec_from_file_location(loader.name, path, loader=loader)
        if spec is None:
            raise ImportError(f"cannot create a spec for the module at {path}", name=loader.name, path=path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        try:
            self._mut_recording = macro_recording(_expansion_or(loader, path, module)).__enter__()
        except BaseException:
            self._restore()
            raise
        return self._mut_recording

    def __exit__(self, *exc_info: object) -> None:
        try:
            if self._mut_recording is not None:
                self._mut_recording.__exit__(*exc_info)
        finally:
            self._restore()

    def _restore(self) -> None:
        if self._present is None:
            sys.modules.pop(self._loader.name, None)
        else:
            sys.modules[self._loader.name] = self._present


def file_sha256(path: str) -> str | None:
    """sha256 of a file's contents (None if unreadable) -- so the side that builds records and the side that checks them use the same one."""
    import hashlib  # not loaded at startup

    try:
        status = os.stat(path)
    except OSError:
        return None
    key = (path, status.st_mtime_ns, status.st_size)
    digest = _digests.get(key)
    if digest is None:
        try:
            with open(path, "rb") as source:
                digest = hashlib.sha256(source.read()).hexdigest()
        except OSError:
            return None
        _digests[key] = digest
    return digest


# Leave the return type to inference from the inner function -- it has the same shape as ``SourceFileLoader.source_to_code`` down to argument names, so it can be put straight back into the entry
# (writing it as a Callable alias would drop the argument names, and assigning to the entry would become a type mismatch).
def _recording_source_to_code(previous: SourceToCode):
    """Wrapper for the compile entry -- right after a Hy module is compiled, canonicalize gensym names and add the record of the macros the expansion depended on to the code."""

    def source_to_code(
        self: importlib.machinery.SourceFileLoader,
        data: object,
        path: object,
        *args: object,
        **kwargs: object,
    ) -> CodeType:
        code = previous(self, data, path, *args, **kwargs)
        if not isinstance(path, str) or not is_hy_source(path):
            return code
        from doeff_hy_bytecode_guard import _records as records  # load only when Hy source is encountered

        # Compiling Hy source -- import, rebuild, the precompile tool (source_to_code_as_import), and image assembly (which calls source_to_code
        # directly) all pass through this entry -- above or below Hy's entry, what it receives here is already-compiled code. Make gensym names sequential numbers
        # within the module so the code does not depend on compile order, process, or thread.
        code = records.canonical_gensyms(code)
        counts = _counts()
        counts[path] = counts.get(path, 0) + 1
        module = _module_being_loaded(self, path)
        if module is None:
            # A compile outside a module (py_compile, image-assembly warm) -- what macros were required is
            # not visible, so no record is added. Bytecode without a record is recompiled on the next load when Python matches it against the source (get_code).
            # Forms that are not matched (image hash mode) are trusted as is. The tool that precompiles bytecode
            # compiles inside the module via source_to_code_as_import and attaches a record. runpy loads (``hy <script>``, ``hy -m``)
            # have the load entry place the module and wrap it, so they do not come here (_expansion_recording).
            return code
        if TYPE_CHECK_EXPANSION.get():
            # Code from expansion for type checking (doeff_hy._static_view) cannot run (it lacks the isinstance of :pre and the runtime import) --
            # no record is attached. Code without a record does not enter the store (_to_shared_store), and even if written to .pyc the next normal import
            # recompiles it (_record_is_current_here). If attached, it would remain under the same key as normal expansion and a normal import would read it and crash (I-3).
            return code
        from doeff_hy_bytecode_guard import _macro_use as macro_use  # load only when Hy source is encountered

        table = vars(module).get(records.MACRO_TABLE)
        if not isinstance(table, macro_use.RecordingTable):
            # A compile outside the contexts that collect used macros (macro_recording in get_code and source_to_code_as_import) -- what macros were used is
            # not visible, so no record is added (code without a record is recompiled on the next load).
            return code
        # The statements of the passed source (below Hy's entry, the already-expanded tree arrives -- then read from the bytes the load entry remembered, or from the file).
        source = macro_use.source_text(_last_sources().get(path, data), path)
        if source is None:
            return code
        recording = table.recording
        if recording is None:
            return code
        record = recording.record(source)
        # Below Hy's entry the already-expanded tree arrives -- put it next to the record so doeff-effect-analyzer does not re-expand the same source.
        # The owner of the placement dir is expansion_trees (not trees; if the dir is absent nothing is placed).
        from doeff_hy_bytecode_guard import _expansion_trees as expansion_trees

        expansion_trees.add_import_tree(module.__name__, source, record, data)
        return records.with_record(code, record)

    return source_to_code


# Leave the return type to inference from the inner function (same reason as above).
def _remembering_get_data(previous: GetData):
    """Wrapper for the load entry -- remember the bytes of the Hy source read, as this thread's "last read contents". The compile entry
    inside Hy's wrapper receives only the expanded tree, so the compiled bytes are visible only at the read site. The store key is
    built from these bytes, without re-reading the file."""

    def get_data(self: importlib.machinery.SourceFileLoader, path: str) -> bytes:
        read = _read_pycs().pop(path, None) if _pyc_reads else None
        if read is not None:
            return read  # the .pyc the load entry already read for its header (_bytecode_header)
        data = previous(self, path)
        if is_hy_source(path):
            _last_sources()[path] = data
        return data

    return get_data


# Leave the return type to inference from the inner function (same reason as above).
def _scoped_exec_module(previous: ExecModule):
    """Wrapper for the module-body execution entry -- during the outermost execution (including imports inside it), set up a memory so the .pyc record check reads file state only once
    (the check runs per Hy module, and checking each time stat'ed the same file hundreds of times -- agora-redesign).
    A nested execution uses the outer memory. On close the memory is closed -- values remain in threads and copied contexts that arose inside.
    During the same span, raise the GC threshold (:mod:`doeff_hy_bytecode_guard._import_gc` -- loading swept the whole heap each time it grew)."""

    def exec_module(self: importlib.machinery.SourceFileLoader, module: ModuleType) -> None:
        _note_executed(self.path)
        if current_scope() is not None:
            return previous(self, module)
        scope = ImportScope()
        token = IMPORT_SCOPE.set(scope)
        import_gc.hold()
        try:
            return previous(self, module)
        finally:
            import_gc.release()
            scope.close()
            IMPORT_SCOPE.reset(token)

    return exec_module


def _note_executed(path: object) -> None:
    """Remember the current state of the source file whose body is being executed (an unreadable file clears the memory) -- so the check step can verify that the module's values and the current file's
    contents are from the same point in time (:func:`executed_source_is_current`)."""
    if not isinstance(path, str):
        return
    try:
        status = os.stat(path)
    except OSError:
        _executed.pop(path, None)
        return
    _executed[path] = (status.st_mtime_ns, status.st_size)


def executed_source_is_current(path: str) -> bool:
    """Whether the file at path is still in the state (mtime ns, size) it had when this process's load entry last executed that module's body (false if unreadable).
    A module loaded without going through the load entry (startup before the wrap, extension modules) has nothing to check against, so true. One load
    stats the current disk rather than a copy (verifying the check's memory) -- the check step then reads statements and sha256 from the current disk, so
    they are compared at the same point in time (a few files per step -- not per check)."""
    executed = _executed.get(path)
    if executed is None:
        return True
    try:
        status = os.stat(path)
    except OSError:
        return False
    return (status.st_mtime_ns, status.st_size) == executed


# Leave the return type to inference from the inner function (same reason as above).
def _checking_get_code(previous: GetCode):
    """Wrapper for the load entry -- if the expansion of a Hy module read from .pyc depends on a stale macro, recompile it."""

    def get_code(self: importlib.machinery.SourceFileLoader, fullname: str) -> CodeType | None:
        path = self.get_filename(fullname)
        if not is_hy_source(path):
            return previous(self, fullname)
        _remember_dataclass_code()  # after a Hy import, dataclasses is already loaded
        try:
            code = _hy_code(self, fullname, path)
        finally:
            _last_sources().pop(path, None)  # the remembered source is used only during this read
            _read_pycs().pop(importlib.util.cache_from_source(path), None)  # likewise the .pyc read for the header
        record = _remember_record(path, code)
        if code is not None and record is not None:
            # Tell the one line to add to a module where a macro was forgotten to be required, or was brought in by import (before execution -- Python emits the statement and
            # that frame has no doeff code). The same record is attached to code read from .pyc or the store.
            from doeff_hy_bytecode_guard import _macro_hints as macro_hints  # load only when Hy source is encountered

            macro_hints.warn_about_macro_use(code, record, path, fullname)
        return code

    def _hy_code(self: importlib.machinery.SourceFileLoader, fullname: str, path: str) -> CodeType | None:
        """Code for Hy source -- returns, from the store, .pyc, or compile, one whose expansion matches the current macros."""
        header = _bytecode_header(self, path)
        shared = _from_shared_store(self, fullname, path, header)
        if shared is not None:
            return shared  # code with the same contents built in another worktree -- the record was verified against the macro files of the current environment
        counts = _counts()
        before = counts.get(path, 0)
        if _bytecode_matches_source(self, path, header):
            # The worktree .pyc matches the source -- the standard entry only reads .pyc and does not compile, so it does not look up the expansion module (the lookup
            # walks all loaded modules. Looking up per Hy module was a fixed cost of about 0.6 seconds of CPU in loading a check).
            # When the .pyc is broken and gets compiled there is no record, so the check below recompiles inside the context.
            code = previous(self, fullname)
        else:
            # When recompiling (.pyc missing or stale), collect the macros the expansion used (the compile-entry wrapper builds the record).
            with _expansion_recording(self, path):
                code = previous(self, fullname)
            if code is None or counts.get(path, 0) != before:
                if code is not None:
                    _to_shared_store(fullname, path, _compiled_source(path), code)
                return code  # just compiled -- the macros it depended on are the current files
        if code is None:
            return None
        from doeff_hy_bytecode_guard import _records as records  # load only when Hy source is encountered

        if header is not None and not records.python_checks_source(
            header, _check_hash_based_pycs()
        ):
            # A .pyc that Python does not match against the source (PEP 552 unchecked-hash -- the form image assembly bakes)
            # does not trust macro changes, just as Python does not trust source changes (on the premise it is rebaked during assembly).
            # Checking the record would not use the outcome, so return before checking.
            return code
        if _record_is_current_here(code):
            return code
        return _replacing_stale_pyc(self, fullname, path, header)

    return get_code


def _replacing_stale_pyc(
    self: importlib.machinery.SourceFileLoader, fullname: str, path: str, header: bytes | None
) -> CodeType:
    """Code that replaces a .pyc whose record is stale -- if the store has an entry matching the current environment's macros, use it (without compiling, and rewrite the .pyc),
    otherwise recompile and add to the store."""
    stored = _from_shared_store_over_stale_pyc(self, fullname, path, header)
    if stored is not None:
        return stored
    with _expansion_recording(self, path):
        recompiled = _recompile(self, path, header)
    _to_shared_store(fullname, path, _compiled_source(path), recompiled)
    return recompiled


def _remember_record(path: str, code: CodeType | None) -> "MacroRecord | None":
    """Remember the record attached to code returned by the load entry (:func:`loaded_record`) -- for code with no record (a compile outside a module, type-check expansion)
    clear the previous memory (do not leave another code's record). Returns the remembered record (None if none)."""
    record = None if code is None else _record_of(code)
    if record is None:
        _loaded_records.pop(path, None)
    else:
        _loaded_records[path] = record
    return record


def _record_is_current_here(code: CodeType) -> bool:
    """Whether the record attached to code matches the current Hy version and the macro files of the current environment (false if there is no record; the check is the same as
    record_is_current_here)."""
    record = _record_of(code)
    return record is not None and record_is_current_here(record)


def _record_of(code: CodeType) -> "MacroRecord | None":
    """The record attached to code -- the load entry reads the record of the same code in succession for the check (:func:`_record_is_current_here`) and the memory (:func:`_remember_record`),
    so reuse the record of the one code read last (building a record verifies the whole field shape -- the import of declaration tools
    built it twice for each of 2,500 modules). The memory is replaced as a single (code, record) tuple, so another code's record is never returned across threads."""
    from doeff_hy_bytecode_guard import _records as records  # load only when Hy source is encountered

    last = _last_record.get("entry")
    if last is not None and last[0] is code:
        return last[1]
    record = records.record_of(code)
    _last_record["entry"] = (code, record)
    return record


def bytecode_is_current(path: str, pyc: bytes, source: bytes) -> bool:
    """Whether the contents pyc of a .pyc already in the tree can be used as is for the current source and the current environment's macros -- so the tool that precompiles bytecode decides whether to keep a .pyc carried over
    from a previous tree without rebaking it.

    Usable only if it is a PEP 552 hash-based .pyc with a source hash in its header and that hash equals the current source. For a .pyc of Hy source,
    the record must also match the current environment's macros (same check as the load entry). If the tool does not rebake here,
    the first import of a version with changed macros recompiles all carried-over Hy modules."""
    from doeff_hy_bytecode_guard import _records as records  # load only when the tool hits a carried-over .pyc

    if len(pyc) < records.PYC_HEADER_BYTES or pyc[:4] != importlib.util.MAGIC_NUMBER:
        return False
    flags = int.from_bytes(pyc[4:8], "little")
    if not flags & records.FLAG_HASH_BASED or pyc[8:16] != importlib.util.source_hash(source):
        return False
    if not is_hy_source(path):
        return True
    import marshal  # load only when a Hy .pyc is encountered

    try:
        code = marshal.loads(pyc[records.PYC_HEADER_BYTES :])
    except (EOFError, ValueError, TypeError):
        return False  # a .pyc with broken contents is unusable (rebake)
    return isinstance(code, CodeType) and _record_is_current_here(code)


def _store_key(fullname: str, path: str, source: bytes) -> str:
    """The code key determined by the source contents (the key and ordering are defined in one place, code_store -- one key holds entries per macro environment)."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # load only when using the store

    return code_store.code_key(
        path, source, fullname, _hy_version(), sys.implementation.cache_tag or "", sys.flags.optimize
    )


def _from_shared_store(
    self: importlib.machinery.SourceFileLoader, fullname: str, path: str, header: bytes | None
) -> CodeType | None:
    """When the worktree .pyc is unusable, look up code with the same contents built in another worktree from the store (code_store -- looked up by contents across worktrees and versions)
    (None if not found) -- the new worktree's .pyc is tied to the source's absolute path so it is always cold, and the transformation (macro
    expansion) of files with the same contents was redone.

    Check the records of the key's entries (arranged per macro environment -- including those by writers of different doeff versions) by looking up the used macros again by module name in the current environment,
    and use one that matches (records have no file paths, so they can be written as is to the worktree .pyc). The worktree .pyc is also written in the standard timestamp form
    ."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # load only when using the store

    store = code_store.store_dir()
    if store is None:
        return None
    from doeff_hy_bytecode_guard import _records as records  # load only when using the store

    try:
        stats = self.path_stats(path)
    except OSError:
        return None
    mtime, size = int(stats["mtime"]), int(stats["size"])
    if header is not None and (
        records.timestamp_header_matches(header, mtime=mtime, size=size)
        or int.from_bytes(header[4:8], "little") & records.FLAG_HASH_BASED
    ):
        return None  # the worktree .pyc is usable (or is hash mode -- left to Python's own decision)
    return _stored_code(self, fullname, path, mtime=mtime, size=size, previous_header=None)


def _from_shared_store_over_stale_pyc(
    self: importlib.machinery.SourceFileLoader, fullname: str, path: str, header: bytes | None
) -> CodeType | None:
    """When the worktree .pyc matches the source but its record does not match the current environment's macros (a tree reused after swapping the sibling doeff), look in the store
    before recompiling (None if not found, and the caller compiles as usual and adds) -- even though the store had an entry matching the current environment, the mere existence of a .pyc
    meant it was not looked up and all Hy modules in the tree were recompiled. The check is the same as :func:`_from_shared_store`.
    The worktree .pyc is rewritten in the same mode as the .pyc that was read (timestamp or hash)."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # load only when using the store

    if code_store.store_dir() is None:
        return None
    try:
        stats = self.path_stats(path)
    except OSError:
        return None
    return _stored_code(
        self, fullname, path, mtime=int(stats["mtime"]), size=int(stats["size"]), previous_header=header
    )


def _stored_code(
    self: importlib.machinery.SourceFileLoader,
    fullname: str,
    path: str,
    *,
    mtime: int,
    size: int,
    previous_header: bytes | None,
) -> CodeType | None:
    """Of the entries under the store key, use one whose record matches the current environment's macros (None if none). Rewrite the code's file name to the current source path,
    and write the worktree .pyc in the mode of previous_header (the standard timestamp if None) (not written under a configuration that does not write bytecode)."""
    from doeff_hy_bytecode_guard import _code_store as code_store, _records as records  # load only when using the store

    store = code_store.store_dir()
    if store is None:
        return None
    try:
        source = self.get_data(path)
    except OSError:
        return None  # if the source cannot be read, the standard get_code reports the same error
    code = code_store.current_code(store, _store_key(fullname, path, source), _record_is_current_here)
    if code is None:
        return None
    import _imp  # rewrite the code's file name to the current source path, through the same entry as the standard _compile_bytecode

    _imp._fix_co_filename(code, path)
    if not sys.dont_write_bytecode:
        import contextlib

        data = records.pyc_bytes(code, source=source, mtime=mtime, size=size, previous_header=previous_header)
        with contextlib.suppress(OSError):
            self.set_data(importlib.util.cache_from_source(path), data)
    return code


def _compiled_source(path: str) -> bytes | None:
    """The bytes of the Hy source just compiled -- the contents this thread read just before compile (what the load entry remembered). When compiled
    without going through the load entry (no memory), None, and nothing is added to the store (the key bytes cannot be said to equal what was compiled)."""
    data = _last_sources().get(path)
    return data if isinstance(data, bytes) else None


def _to_shared_store(fullname: str, path: str, source: bytes | None, code: CodeType) -> None:
    """Add the Hy code just compiled to the store (code_store), keyed by the bytes of the source that was compiled (code without a record is not added.
    Under a configuration that does not write bytecode, add only when a store is named explicitly -- not writing is a decision about writes to the tree, and an explicitly named store is outside the tree)
    -- so the next time a file with the same contents is read in another worktree, the transformation is not redone. The key is built only from the
    compiled bytes, without re-reading the file (re-reading would put old code under the key of the contents of a file rewritten in between).
    Do not add to an unwritable store -- the import entry runs at startup of every Python in the venv, so the reason it cannot write is not printed on every import
    (the store is only for speed, and the worker tool names and prints the same write reason)."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # load only when using the store

    store = code_store.store_dir()
    if store is None or source is None or (sys.dont_write_bytecode and not code_store.store_is_named()):
        return
    from doeff_hy_bytecode_guard import _records as records  # load only when using the store

    if records.record_of(code) is None:
        return
    code_store.add_code(store, _store_key(fullname, path, source), code)


def _recompile(
    self: importlib.machinery.SourceFileLoader, path: str, header: bytes | None
) -> CodeType:
    """Recompile from source in place of the stale expansion (the compile-entry wrapper adds the record), and rewrite in the same mode as the .pyc that was read."""
    from doeff_hy_bytecode_guard import _records as records  # load only when Hy source is encountered

    source = self.get_data(path)
    code = self.source_to_code(source, path)
    if sys.dont_write_bytecode:
        return code
    stats = self.path_stats(path)
    data = records.pyc_bytes(
        code,
        source=source,
        mtime=int(stats["mtime"]),
        size=int(stats["size"]),
        previous_header=header,
    )
    import contextlib  # load only when a stale record is encountered

    # As in the standard get_code, in an unwritable place (a read-only tree) use the compiled code without writing.
    with contextlib.suppress(OSError):
        self.set_data(importlib.util.cache_from_source(path), data)
    return code


def _bytecode_header(self: importlib.machinery.SourceFileLoader, path: str) -> bytes | None:
    """The header of the .pyc next to the source (None if absent) -- look at the mode (timestamp / hash, whether checked) so it can be rewritten in the same mode.
    The whole .pyc is read here once and handed to the standard get_code's own read of the same file (:func:`_read_pycs` -- the load entry wrapper returns these
    bytes instead of opening the file again). Opening it twice, once for the header and once for the body, cost about 0.4 seconds of CPU over the 3,000 Hy
    modules the agora-controllers declaration tool loads."""
    from doeff_hy_bytecode_guard import _records as records  # load only when Hy source is encountered

    pyc_path = importlib.util.cache_from_source(path)
    try:
        with io.open_code(pyc_path) as pyc:
            data = pyc.read()
    except OSError:
        return None
    _read_pycs()[pyc_path] = data
    return data[: records.PYC_HEADER_BYTES]


def _bytecode_matches_source(
    self: importlib.machinery.SourceFileLoader, path: str, header: bytes | None
) -> bool:
    """Whether the worktree .pyc (header) matches the current source's mtime and size in the standard timestamp form -- if so the standard get_code only reads the
    .pyc and does not compile. The hash form is left to Python's own decision and is not deemed a match (counted on the side that may compile)."""
    if header is None or header[:4] != importlib.util.MAGIC_NUMBER:
        return False
    from doeff_hy_bytecode_guard import _records as records  # load only when Hy source is encountered

    try:
        stats = self.path_stats(path)
    except OSError:
        return False
    return records.timestamp_header_matches(header, mtime=int(stats["mtime"]), size=int(stats["size"]))


def _expansion_recording(
    self: importlib.machinery.SourceFileLoader, path: str
) -> "contextlib.AbstractContextManager[object]":
    """A context around a load that may compile -- collect the macros used by expansion in the table of the module where expansion runs (:func:`_module_being_loaded`). The module is
    looked up only when it may compile (the lookup walks all loaded modules).

    The module is absent when the load entry is called without going through an import (runpy's ``hy <script>`` and ``hy -m`` call the loader's get_code before putting the module in
    ``sys.modules``). Collect inside a module placed under that name (:class:`_PlacedExpansion`) -- otherwise Hy compiles with a temporary module
    and deletes it right after compile, so a .pyc without a record is recompiled every time as "stale" and never enters the store.
    If a module of another file uses the same name (``hy json.hy`` -- Hy itself also rewrites the ``__file__`` of an existing module and removes it), do not place it and
    collect nothing."""
    import contextlib  # load only when it may compile

    module = _module_being_loaded(self, path)
    if module is not None:
        return macro_recording(module)
    if self.name in sys.modules:
        return contextlib.nullcontext()
    return _PlacedExpansion(self, path)


def _module_being_loaded(
    self: importlib.machinery.SourceFileLoader, path: str
) -> ModuleType | None:
    """The module in which Hy compile expansion runs -- a require inside the compile puts macros in this module's table, and expansion looks up this table, so it is the source of the record.

    The lookup is the same as Hy's compile entry (``hy.importer.loader_module_obj``): look up the loaded module from the file name
    (``inspect.getmodule`` -- if a module read the same file earlier under another name, that one), otherwise the module in the middle of importing
    (``self.name``, placed in ``sys.modules`` beforehand). Looking up by ``self.name`` alone, in a process that read the same file under the package name and then
    re-read it from the path under another name (bake_plan.hy of the worker bytecode tool), built an empty record from a table that expansion does not consult, and the check
    put a pass-through entry in the shared place (2026-10-11 atlas)."""
    import inspect  # load only when Hy source is encountered (Hy's compile entry also loads it)

    try:
        module = inspect.getmodule(None, _filename=self.path)
    except KeyError:  # same as Hy's entry -- a module that vanished from sys.modules during the lookup is treated as absent
        module = None
    if module is None:
        module = sys.modules.get(self.name)
    if module is None or vars(module).get("__file__") != path:
        return None
    return module


def _expansion_or(
    loader: importlib.machinery.SourceFileLoader, path: str, placed: ModuleType
) -> ModuleType:
    """The module in which expansion runs in a compile outside an import -- the loaded module Hy looks up from the file name (:func:`_module_being_loaded`) takes precedence over the placed module placed.
    So that the recording context opens on the module of the table that expansion consults."""
    return _module_being_loaded(loader, path) or placed


def _read_pycs() -> dict[str, bytes]:
    """The table of .pyc contents this thread's load entry read for the header and has not yet handed to the standard get_code (path of the .pyc -> bytes;
    created if first time). Emptied for the module when its load entry returns, so bytes are never handed to a later read of a rewritten file."""
    import threading  # not loaded at startup

    local = _pyc_reads.get("local")
    if not isinstance(local, threading.local):
        local = _pyc_reads.setdefault("local", threading.local())
    return vars(local).setdefault("pycs", {})


def _last_sources() -> dict[str, bytes]:
    """The table of Hy sources last read in this thread (created if first time)."""
    import threading  # not loaded at startup

    local = _source_reads.get("local")
    if not isinstance(local, threading.local):
        local = _source_reads.setdefault("local", threading.local())
    return vars(local).setdefault("sources", {})


def _counts() -> dict[str, int]:
    """The table of compile counts for this thread (created if first time)."""
    import threading  # not loaded at startup

    local = _compile_counts.get("local")
    if not isinstance(local, threading.local):
        local = _compile_counts.setdefault("local", threading.local())
    return vars(local).setdefault("counts", {})


def _hy_version() -> str:
    """The loaded Hy version -- so that raising Hy recompiles all Hy modules (empty if there is no Hy)."""
    hy = sys.modules.get("hy")
    version = None if hy is None else vars(hy).get("__version__")
    return version if isinstance(version, str) else ""


def _check_hash_based_pycs() -> str:
    """The value of ``--check-hash-based-pycs`` -- so the value that decides whether the standard get_code checks hash-based .pyc is read from the same place."""
    import _imp  # the sole definition source of this value (the standard importlib._bootstrap_external reads the same place)

    return _imp.check_hash_based_pycs
