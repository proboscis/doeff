"""How the record of the macros an expansion used is built and checked (:class:`doeff_hy_bytecode_guard._records.MacroRecord`).

Collecting the macros used: only during expansion (compile), the macro table of the module being expanded (``_hy_macros``) is replaced with a
:class:`RecordingTable` (a dict that remembers the names looked up). Hy 1.3.1's ``hy.macros.macroexpand`` checks the expanded module's table with ``name in table``
and looks up ``table[name]``, so the names looked up are the macros used, and the names not in the table are the "names looked up but absent". Even when a macro
expands into a call to another macro, it is looked up from the same table. A macro required inside a function goes into a local table (the compiler's local state)
that Hy's expansion consults before the module's table, so we wrap the entry point through which the compiler opens local state, remember the local tables
(:func:`_watch_local_tables`), and count the macros executed during expansion within them as used macros.

Macro digest = digest of the closure of the macro function's code (:class:`_Walker`):
- The contents of the code object (instructions, constants — nested code by the same rules, the name tables, argument counts, flags). Line numbers and positions (co_firstlineno,
  co_linetable, co_positions) are excluded — adding lines to a macro's module does not change the digest of other macros.
- Follow globals looked up by name (values obtained by looking the code's name table up in the function's ``__globals__``), defaults, and closure cells. Functions and classes (a class
  through its methods) are walked recursively by the same rules. Plain values, and tuples and sets of plain values, are included by value. A module contributes its name and the attributes that appear in the code's
  name table (reads of ``module.name``). String constants in code are treated as candidate names, and the same-named attribute of the walked module is included too
  (reads like ``getattr(module, "name")`` — covered even when the reading function and the module reference live in different functions). However, the module's docstring
  (``__doc__``) is not included (the "__doc__" in expansion code is a marker for writing a docstring into the expansion result; the module's own docstring does not affect the expansion result).
  Attributes that merely name a module's location (``__file__``, ``__cached__`` — :data:`_LOCATION_NAMES`) contribute only a marker, not the value (the path of the working
  tree), even when encountered by name (the same tree placed in a different dir keeps the same digest).
  The digest of code or a binding statement that reads the location as a value (the form ``Path(__file__).with_name(...)`` that reads a neighboring file) cannot cover the contents of that file,
  so it is marked as unverifiable (the same as environment variable reads: :data:`doeff_hy_bytecode_guard._records.ENVIRONMENT_READ`).
- The code of a function is covered only for functions actually executed during that module's expansion (the closure walk of defk walked every method of the classes reachable from defk
  (binding_forms._Walker), and a change to the _assign code, which expansion never calls, caused 1,197 users of defk to be
  rebuilt). The walk still follows everything as before (function names, class member names, values, binding statements, function globals and import targets), but the code contents at the place where a function
  is first encountered (:attr:`CodeFacts.shape`) are kept only for functions the record names as covered; the others get the marker :data:`_UNCOVERED`
  (:class:`ClosureWalk`). Covered functions = functions of code whose execution started during expansion (inside the ``with`` of :class:`MacroRecording`)
  (collected by :mod:`doeff_hy_bytecode_guard._called_code`), plus the functions that are not narrowed. The record holds the covered functions as bits indexed by the order in which the walk encountered functions
  (:attr:`doeff_hy_bytecode_guard._records.UsedMacro.covered`), and verification rebuilds the current closure digest with the same markers and compares.
  The code of a function that was not executed does not change the expansion result, so dropping its contents does not cause verification to judge an old expansion as matching — if the contents of the functions that were executed
  and the values they read are the same as in the record, the expansion takes the same path (to change which function is called, the contents of an executed function or a value it reads must change).
  Functions that are not narrowed:
  - What lies beyond binding statements (functions and classes encountered inside :meth:`_Walker._bound`, and what is reachable from them) — the code that produced the value ran outside the expansion (at load
    time or in an earlier expansion), and its contents cover the value (functions that rewrite it, functions that the binding statement calls). Things encountered a second time (only the number is added) are
    also not narrowed — for each function and class, we remember what was encountered while walking its contents and follow it.
  - When covering code that reads a function's contents as a value (:data:`_CODE_READ_NAMES` — ``__doc__``, ``__code__``, ``inspect.signature``, etc.),
    all the contents of the closure's functions are covered (the docstring and argument shape of a function that is not executed affect the expansion result).
  - All rows in environments where collection is impossible (up to Python 3.11, or the sys.monitoring tool id is not free), records made outside ``with``, and values outside the macro.
  Forms not looked at: memory in which a function of an outside module writes into another module's global table across expansions (the writer does not fall beyond a binding statement, and if the current expansion does not call the function that filled
  the table in an earlier expansion, it is not covered — tables written by functions or classes of the same module and functions wrapped by ``functools.lru_cache`` are covered),
  ``getattr(f, "__doc__")`` with a string lookup (the string constant "__doc__" in expansion code is a marker for writing a docstring into the expansion result), and the contents of functions the standard
  library and Hy read internally. If the macro's module is first read inside the enclosure, functions executed at load time are also covered, so the markers in a record of the same
  source can differ between processes (both records match under verification).
- Hy itself (``hy.*``), the Python standard library, and builtins contribute only the name (covered by the Hy version and the Python version).
- Import targets inside functions, targets of hy.R calls, and modules under a package: import them and include the attributes named in the name table (covering the whole module
  by file would also cover doeff_hy.macros, which defhandler imports in its body, and any line change in macros.hy would go cold). Verification
  runs in the middle of reading the .pyc (in the middle of an import), so an import target inside a function is a branch not taken at expansion time and may fail by requiring a module that is in the middle of a circular
  import — when it fails, only the name is included.
- Digest of in-the-middle-of-import state: a digest where the walk hit a module in the middle of an import (its namespace not yet complete)
  or a module that cannot be imported depends on the progress of imports — which process read what in which order
  (doeff_hy._outcome_forms in deff's closure cannot be imported while doeff_hy.record is being read from a .pyc, and in a process where loading is finished
  it is walked including its contents). That digest is not put into the process memory and not into the record (None). Instead the record holds the sha256 of the files of the modules the closure depended on
  (:attr:`MacroRecord.files`), and for rows where either the record or the current walk hit an in-the-middle-of-import module,
  the comparison uses those files instead of the digest. When both are in the fully loaded state, the digest is compared (the file rows are not looked at).
- Memory across processes (clause B1 of packages/doeff-hy/architecture.hy): the digest is remembered only
  within a process (:data:`_REMEMBERED`). The digest depends on live values (plain values coming from other modules, values determined by environment variables), and the state of the walked files alone
  is not enough to trust it, so a new process walks the closure again. The reading of source statements, which was most of the cost of walking again, is remembered in storage as
  a pure read determined by contents (:mod:`doeff_hy_bytecode_guard._bound_source`).
- Reading environment variables (present since the baseline version cf7ea9cc0):
  The standard library contributes only the name (covered by the version), but ``os.environ`` is process state and is covered by neither the version nor files. A table built from environment variables is covered by the source statement that binds the name, and the statement does not hold the environment variable values, so
  a process that changed only the environment variables judged the old expansion as matching the present. A digest where the walk hit an environment variable read — code or a binding statement looking up the os (posix, nt) module by a global name
  and reading ``environ``, ``environb``, ``getenv``, or ``getenvb``, reading it through an import of os inside a function, or meeting those objects as values
  (an aliased import, a default, a closure cell) — cannot be verified: it is not remembered, and the record row gets the marker
  :data:`doeff_hy_bytecode_guard._records.ENVIRONMENT_READ` (verification always judges it stale, and users expand again in each process).
  Plain-value globals built from environment variables (strings, numbers) enter by value, so this rule does not apply to them. When covering a value that cannot be walked by the file of its owning module
  (defaults, closure cells, globals whose binding cannot be followed, read targets that cannot be followed by name — :meth:`_Walker._fold_opaque`), it is marked if the owner's source
  contains both the name of a module holding environment variables and the name of an attribute that reads environment variables — the file does not capture environment variables read at load time.
  Reads not looked at: environment variables the standard library functions read internally (``os.path.expanduser``,
  ``tempfile``, etc.), reads by string lookup (``getattr(os, "environ")``), reads through the module table (``sys.modules["os"]``,
  ``hy.I.os``), and tables bound by a star import (covered by the owner's file; the source of the import target is not read).
- Tables that enum and namedtuple build from a class definition (``_member_map_``, etc.) are not included (they are followed as the members and the defaults of ``__new__``).
- Covering values that cannot be walked narrowly:
  - An unwalkable value looked up by a module global name (list, dict, an object that is none of the above) is covered by the source statements that bind and rewrite that name
    (:mod:`doeff_hy_bytecode_guard._bound_source` — it also follows the names the statement reads and the top-level functions that may rewrite it in their body).
  - The value of a frozen dataclass enters as the class and the field values, and a ContextVar as its name and default (the current value is execution state, so it is not included).
  - A class of an extension module (a class with no Python source) is covered not by the .so file but by its name and public shape (bases, attribute names and kinds).
  - A function's kwdefaults (a dict of name to default) enter by value in name order.
- Fail on the safe side: unwalkable values reached not by a global name (defaults, closure cells, inside tuples, class values) and globals of a module whose source cannot be read
  get the sha256 of that module's file added. Reads that cannot be followed by name (:class:`NamespaceRead`) are split by whether they read as a global name or as an attribute:
  a function that reads a module namespace (``globals()``, ``vars``, ``sys.modules``, ``import_module``, ``eval``) gets the file of that function's
  module and the files of the modules the function references added; a function that reads an object namespace (``x.__dict__``, ``__getattribute__``) gets
  the files of the modules the function references added (a field name ``x.vars`` is not a read).
"""

import bisect
import contextvars
import dataclasses
import dis
import enum
import functools
import hashlib
import importlib
import importlib.machinery
import importlib.util
import io
import operator
import os
import re
import struct
import sys
import threading
import types
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import chain, pairwise
from types import CodeType, FunctionType, ModuleType

from doeff_hy_bytecode_guard import _bound_source as bound_source
from doeff_hy_bytecode_guard import _called_code as called_code
from doeff_hy_bytecode_guard import _loader_hooks as loader_hooks
from doeff_hy_bytecode_guard._import_scope import IMPORT_SCOPE, ImportScope, current_scope
from doeff_hy_bytecode_guard._records import (
    ANON_VAR_NAME,
    ENVIRONMENT_READ,
    GENERATED_NAME_PREFIX,
    LOCAL_MACRO_TABLE,
    MACRO_TABLE,
    READER_TABLE,
    MacroRecord,
    UsedFile,
    UsedMacro,
    UsedValue,
    ValueReference,
    WholeRequire,
    exported_macro_names,
)

Sha256Of = Callable[[str], "str | None"]


class RecordingTable(dict[str, object]):
    """Stand-in for the module's macro table — remembers the names Hy's expansion looked up (the macros used) and the names it looked up that were absent. Macros that require
    adds to the table go in as they are (the ``dict`` write entry points are not replaced)."""

    def __init__(self, entries: dict[str, object]) -> None:
        super().__init__(entries)
        self.used: set[str] = set()
        self.absent: set[str] = set()
        #: The recording that opened this table (so the compile entry wrapper can build a record from the table).
        self.recording: MacroRecording | None = None
        #: Local macro tables the Hy compiler opened during this module's expansion (added by :func:`_watch_local_tables` — the tables into which a require
        #: or defmacro inside a function puts macros. Hy's expansion consults these tables before the module's table, so macros looked up from here
        #: do not go through :meth:`__getitem__`).
        self.local_tables: tuple[dict[str, object], ...] = ()

    def __getitem__(self, name: str) -> object:
        """The entry point where Hy's expansion looks up a macro — remembers the looked-up name as a used macro."""
        value = super().__getitem__(name)
        self.used.add(name)
        return value

    def __contains__(self, name: object) -> bool:
        """The entry point where Hy's expansion checks whether a name is a macro — remembers names that were absent."""
        found = super().__contains__(name)
        if not found and isinstance(name, str):
            self.absent.add(name)
        return found


#: Whether the entry point through which Hy's compiler opens local state has been wrapped (one per process). The wrapper rewrites Hy's compiler class, a process-wide shared object,
#: so this module is the single owner. Under free-threaded Python compiles run concurrently, so the wrapping is done only once, under a lock.
_LOCAL_WATCH = {"installed": False}
_LOCAL_WATCH_LOCK = threading.Lock()


def _watch_local_tables() -> None:
    """Each time Hy's compiler opens local state (a function body, etc.), make the recording table of the module being expanded
    (:class:`RecordingTable`) remember that state's macro table (wrapped once per process). Macros brought in by a require inside a function are looked up from the local table
    and do not go through the module's table, so without this they would be missing from the record, and users kept running an old expansion even after the macro changed.
    The local table is remembered as a plain dict — Hy's require tells local tables apart
    by ``type(target) is dict``, so it cannot be replaced with a table that remembers looked-up names."""
    with _LOCAL_WATCH_LOCK:
        if _LOCAL_WATCH["installed"]:
            return
        # Read Hy only when compiling Hy source (do not load Hy at startup).
        from hy.compiler import HyASTCompiler

        opened = HyASTCompiler.new_local_state

        def new_local_state(compiler: HyASTCompiler) -> None:
            """Open Hy's local state and make the recording table of the module being expanded remember its macro table (the wrapped entry point)."""
            opened(compiler)
            # The bottom-most state is opened before the compiler places the module (a require directly under the module goes into the module's table).
            module = getattr(compiler, "module", None)
            table = None if module is None else vars(module).get(MACRO_TABLE)
            if isinstance(table, RecordingTable):
                table.local_tables = (*table.local_tables, compiler.local_state_stack[-1][LOCAL_MACRO_TABLE])

        HyASTCompiler.new_local_state = new_local_state
        _LOCAL_WATCH["installed"] = True


class MacroRecording:
    """For the duration of one expansion, replace the module's macro table with a :class:`RecordingTable` (the body of ``with`` is the expansion). The record can still be built
    with :meth:`record` after exiting. When opened nested for the same module, the outer recording is used as is (the table is not replaced). Inside ``with``, code whose
    execution started is also collected (:mod:`doeff_hy_bytecode_guard._called_code` — to narrow the functions whose code the digest covers
    to those executed during expansion)."""

    def __init__(self, module: ModuleType, hy_version: str, sha256_of: Sha256Of) -> None:
        self._module = module
        self._hy_version = hy_version
        self._sha256_of = sha256_of
        _watch_local_tables()
        namespace = vars(module)
        present = namespace.get(MACRO_TABLE)
        self._owner = not isinstance(present, RecordingTable)
        self._had_table = MACRO_TABLE in namespace
        self.table = (
            present
            if isinstance(present, RecordingTable)
            else RecordingTable(present if isinstance(present, dict) else {})
        )
        #: Collection of executions during the expansion (nothing is collected before entering ``with`` — a record made then covers all the code of the closure's functions).
        self._mut_called: called_code.CalledCode | called_code.Uncollectable = (
            called_code.Uncollectable("not enclosing the expansion (outside with)")
        )
        if self._owner:
            self.table.recording = self

    def __enter__(self) -> "MacroRecording":
        """Before expansion, replace the module's table with one that remembers looked-up names, and start collecting code whose execution starts."""
        if self._owner:
            vars(self._module)[MACRO_TABLE] = self.table
            if isinstance(self._mut_called, called_code.CalledCode):
                # Entered the same enclosure twice — the table keeps the names the earlier expansion looked up, so keep the collected code too and add to it.
                self._mut_called.reopen()
            else:
                self._mut_called = called_code.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """After expansion, restore the module's table to a plain dict (reads outside the expansion are not remembered)."""
        if self._owner and isinstance(self._mut_called, called_code.CalledCode):
            self._mut_called.close()
        namespace = vars(self._module)
        if not self._owner or namespace.get(MACRO_TABLE) is not self.table:
            return
        if self._had_table or self.table:
            namespace[MACRO_TABLE] = dict(self.table)  # leave a plain dict in the module after expansion
        else:
            del namespace[MACRO_TABLE]

    def record(self, source: str | bytes, also: tuple[ValueReference, ...] = ()) -> MacroRecord:
        """The record of macros the expansion used (source = the source statements of what was expanded — reads hy.R calls that do not go through the table; also = where the values that change
        the expansion result from outside the macro live)."""
        name = self._module.__name__
        # Code whose execution started during expansion (executions during the walk that builds the record are not included — the walk does not call the closure's functions).
        match self._mut_called:
            case called_code.CalledCode() as collecting:
                executed: frozenset[CodeType] | None = collecting.codes()
            case called_code.Uncollectable():
                executed = None
        table = dict(self.table)
        found: dict[UsedMacroKey, FunctionType] = {}
        for used in sorted(self.table.used):
            macro = table.get(used)
            if isinstance(macro, FunctionType) and _from_elsewhere(macro, name):
                found[_macro_key(MACRO_TABLE, macro)] = macro
        # Macros that came in through a require inside a function (looked up from the local table, so they are not among the names looked up in the module's table) — only the macros
        # executed during expansion (in environments where collection is impossible, all of the local table — including more only makes it easier to judge stale).
        for local in self.table.local_tables:
            for macro in list(local.values()):
                if (
                    isinstance(macro, FunctionType)
                    and _from_elsewhere(macro, name)
                    and (executed is None or macro.__code__ in executed)
                ):
                    found[_macro_key(MACRO_TABLE, macro)] = macro
        readers = vars(self._module).get(READER_TABLE)
        for macro in readers.values() if isinstance(readers, dict) else ():
            if isinstance(macro, FunctionType) and _from_elsewhere(macro, name):
                found[_macro_key(READER_TABLE, macro)] = macro
        text = source.decode("utf-8", "replace") if isinstance(source, bytes) else source
        # Targets of hy.R calls that do not go through the table are also used macros (macros of its own module are covered by the source).
        for reference in _source_references(text):
            if reference.module == name or _named_only(reference.module):
                continue
            called = _macro_now(MACRO_TABLE, reference.module, reference.name)
            if called is not None:
                found[UsedMacroKey(MACRO_TABLE, reference.module, reference.name)] = called
        macros = tuple(
            (key, macro_digest(key, macro, self._sha256_of).called(executed))
            for key, macro in sorted(found.items(), key=lambda item: item[0].sort_key)
        )
        others = tuple(
            (reference, value_digest(reference, self._sha256_of))
            for reference in sorted(also, key=lambda reference: (reference.module, reference.name))
        )
        depended = frozenset(
            module
            for closure in (*(closure for _, closure in macros), *(closure for _, closure in others))
            if closure is not None
            for module in closure.modules
        )
        return MacroRecord(
            self._hy_version,
            tuple(
                UsedMacro(key.table, key.module, key.name, closure.digest, f"{closure.covered:x}")
                for key, closure in macros
            ),
            tuple(sorted(self.table.absent - table.keys())),
            _whole_requires((table, *self.table.local_tables), name),
            tuple(
                UsedValue(reference.module, reference.name, closure.digest)
                for reference, closure in others
                if closure is not None
            ),
            _closure_files(depended, self._sha256_of),
        )


def record_is_current(record: MacroRecord, hy_version: str, sha256_of: Sha256Of) -> bool:
    """Whether the record matches the current environment — the Hy version, digests of the used macros re-looked-up in the current environment by module name (this imports the defining module
    — the module the user's require imports anyway), digests of values outside the macro, and that names that were looked up but absent are still absent from the provider.
    If any row cannot be compared by digest (the record or the current walk hit a module in the middle of an import), the files of the modules the closure depended on
    (not imported) are also compared."""
    if record.hy_version != hy_version:
        return False
    if current_scope() is not None:
        return _rows_are_current(record, sha256_of)  # inside the outermost load — file state is read only once inside it
    scope = ImportScope()  # outside a load (the tool verifies directly) — memory only for the duration of this verification
    token = IMPORT_SCOPE.set(scope)
    try:
        return _rows_are_current(record, sha256_of)
    finally:
        scope.close()
        IMPORT_SCOPE.reset(token)


def _rows_are_current(record: MacroRecord, sha256_of: Sha256Of) -> bool:
    """Whether the record rows (used macros, values outside the macro, names looked up but absent, files for when digests cannot be compared) match the current environment."""
    by_files = False
    for check in _row_checks(record, sha256_of):
        match check:
            case RowCheck.STALE:
                return False
            case RowCheck.BY_FILES:
                by_files = True
            case RowCheck.SAME:
                pass
    return all(_still_absent(provider, record.absent) for provider in record.providers) and (
        not by_files or all(_file_is_current(used, sha256_of) for used in record.files)
    )


class RowCheck(enum.Enum):
    """Outcome of verifying one digest row of the record (a used macro, or a value outside the macro).

    - SAME = the current digest equals the record.
    - STALE = differs, cannot be looked up in the current environment, the record was made in an in-the-middle-of-import state but can now be compared (the rebuilt record can be compared), or the record or
      the current walk hit an environment variable read (cannot be verified).
    - BY_FILES = the current walk hit a module in the middle of an import — compare by the record's file rows (the files of the modules the closure depended on)."""

    SAME = "same"
    STALE = "stale"
    BY_FILES = "by-files"


def _row_checks(record: MacroRecord, sha256_of: Sha256Of) -> Iterator[RowCheck]:
    """Verify the record's digest rows in order against the current environment (the caller stops at the first stale row — the remaining macros are not imported)."""
    for used in record.macros:
        macro = _macro_now(used.table, used.module, used.name)
        key = UsedMacroKey(used.table, used.module, used.name)
        # Rebuild the current closure digest from the contents of the functions the record covered (and the functions the current walk does not narrow).
        yield _row_check(
            used.digest,
            None
            if macro is None
            else macro_digest(key, macro, sha256_of).covering(int(used.covered, 16)),
        )
    for value in record.values:
        reference = ValueReference(value.module, value.name)
        yield _row_check(value.digest, value_digest(reference, sha256_of))


def _row_check(recorded: str | None, now: "ClosureDigest | None") -> RowCheck:
    """Outcome of verifying the record's digest (None = the record was made while in the middle of an import) against the current closure (None = cannot be looked up in the current environment)."""
    if now is None or ENVIRONMENT_READ in (recorded, now.digest):
        # An environment variable read cannot be verified on either the record side or the current side — even if the current walk hit a module in the middle of an import and ends up comparing by file rows, files do not
        # reflect differences in environment variables.
        return RowCheck.STALE
    if now.digest is None:
        return RowCheck.BY_FILES
    if recorded is None:
        return RowCheck.STALE
    return RowCheck.SAME if now.digest == recorded else RowCheck.STALE


def _file_is_current(used: UsedFile, sha256_of: Sha256Of) -> bool:
    """Whether the files of the modules the closure depended on have the same contents as in the record (modules are not imported)."""
    path = _module_file(used.module)
    return path is not None and sha256_of(path) == used.sha256


def _still_absent(provider: WholeRequire, absent: tuple[str, ...]) -> bool:
    """Whether a name that was looked up but absent (with the prefix removed) is still absent from every fully required provider — if it has been added, the user's next expansion
    would turn calls of that name into macro expansions."""
    module = _imported(provider.module)
    table = None if module is None else vars(module).get(MACRO_TABLE)
    if not isinstance(table, dict):
        return False
    lead = f"{provider.prefix}." if provider.prefix else ""
    return not any(name.startswith(lead) and name[len(lead) :] in table for name in absent)


def _whole_requires(tables: Iterable[dict[str, object]], module_name: str) -> tuple[WholeRequire, ...]:
    """From the user's tables after expansion (the module's table and local tables inside functions), read the forms that require every macro of a provider — per provider
    (the module that defined the macros) and per prefix, if the names that entered the user's table include all of the provider's public names (``_hy_export_macros``, or names not
    starting with ``_``), it is a full require. A require that lists every name by selecting names also counts as full (including names added across tables —
    this only errs toward judging the verification stale)."""
    entered: dict[WholeRequire, set[str]] = {}
    for key, macro in chain.from_iterable(list(table.items()) for table in tables):
        if not (isinstance(macro, FunctionType) and _from_elsewhere(macro, module_name)):
            continue
        owner = str(macro.__module__)
        provider = sys.modules.get(owner)
        entries = None if provider is None else vars(provider).get(MACRO_TABLE)
        if not isinstance(entries, dict):
            continue
        for defined in sorted(entries):
            if dict.__getitem__(entries, defined) is not macro:
                continue
            if key == defined:
                entered.setdefault(WholeRequire(owner, ""), set()).add(defined)
            elif key.endswith(f".{defined}"):
                prefix = key[: -len(defined) - 1]
                entered.setdefault(WholeRequire(owner, prefix), set()).add(defined)
    return tuple(
        sorted(
            (require for require, names in entered.items() if _exported(require.module) <= names),
            key=lambda require: (require.module, require.prefix),
        )
    )


def _exported(module_name: str) -> frozenset[str]:
    """Public macro names of a provider (the names Hy's require ``*`` brings in — the names are read by :func:`records.exported_macro_names`)."""
    module = sys.modules.get(module_name)
    return exported_macro_names({} if module is None else vars(module))


@dataclass(frozen=True)
class UsedMacroKey:
    """Where a used macro lives (table name, defining module name, name within that module's table) — the key for re-looking-up in the current environment at verification time."""

    table: str
    module: str
    name: str

    @property
    def sort_key(self) -> str:
        """Order of record rows (module, table, name)."""
        return f"{self.module}\0{self.table}\0{self.name}"


@dataclass(frozen=True)
class ClosureDigest:
    """Digest of one closure, and the names of the modules the closure depended on (owners of what the walk followed, and modules that could not be imported — they
    become the file rows of the record). The digest is None when the walk hit a module in the middle of an import or a module that cannot be imported (the material then depends on
    import progress — compare by file rows instead), and the marker ENVIRONMENT_READ when it hit an environment variable read (cannot be verified —
    always judged stale).

    When it has a digest, it also holds the material of the closure walk (walk) and the marker of the functions whose contents the digest covers (covered — bit i is 1 if the function numbered i is covered).
    The digest the walk builds covers all the contents of the closure's functions (rows for values outside the macro use this digest), and rows for used macros
    narrow the covered functions with :meth:`called` (at record time) and :meth:`covering` (at verification time)."""

    digest: str | None
    modules: tuple[str, ...]
    walk: "ClosureWalk | None" = None
    covered: int = 0

    def called(self, codes: frozenset[CodeType] | None) -> "ClosureDigest":
        """Digest at record time — covers the functions of code whose execution started during expansion (None = could not be collected — all), plus the contents of functions that are not narrowed."""
        if self.walk is None:
            return self
        covered = self.walk.covered_by(codes)
        return ClosureDigest(self.walk.digest(covered), self.modules, self.walk, covered)

    def covering(self, covered: int) -> "ClosureDigest":
        """Digest at verification time — covers the functions whose contents the record covered (covered) plus the contents of the functions the current walk does not narrow (if the current walk
        has more functions that are not narrowed, their contents enter and the digest differs from the record — judged stale)."""
        if self.walk is None:
            return self
        widened = covered | self.walk.forced
        return ClosureDigest(self.walk.digest(widened), self.modules, self.walk, widened)


@dataclass(frozen=True)
class CodeSlot:
    """One function the closure walk encountered for the first time — its code (looked up at record time to see whether execution started during expansion), and the digest of the code's contents
    (:attr:`CodeFacts.shape`)."""

    code: CodeType
    shape: str


#: Text put in place of the code contents of a function whose contents are not covered (never equal to a hex digest).
_UNCOVERED = "uncovered"


@dataclass(frozen=True)
class ClosureWalk:
    """Material of one closure walk — the bytes of the material split at the function code contents (number of functions + 1 pieces — each string spelled with its length in front),
    the functions in encounter order (slots), the marker of functions not narrowed (forced — beyond binding statements), and the marker of functions that read a function's contents as a value (reads_code)."""

    segments: tuple[bytes, ...]
    slots: tuple[CodeSlot, ...]
    forced: int
    reads_code: int
    #: Cache of digests per marker (within the process — so that the material is not hashed again each time a user with the same marker is verified).
    digests: dict[int, str] = dataclasses.field(default_factory=dict, compare=False, repr=False)
    #: The walk redone with per-annotation reads (``strict`` of :func:`_code_facts`) for when the closure has a function that reads a function's contents as a value. The digest for the case where
    #: a reading function is among the covered functions is built from this (a function's type annotations do not enter the digest of a closure that does not read them).
    strict: "ClosureWalk | None" = dataclasses.field(default=None, compare=False, repr=False)

    @property
    def everything(self) -> int:
        """Marker covering the contents of all functions."""
        return (1 << len(self.slots)) - 1

    def covered_by(self, codes: frozenset[CodeType] | None) -> int:
        """Marker of functions whose contents are covered at record time — functions of code whose execution started during expansion, plus the functions not narrowed. All of them when collection was impossible (None) or when any of the covered functions
        reads a function's contents as a value (the docstring and argument shape of a function that is not called — they affect the expansion even without executing)."""
        if codes is None:
            return self.everything
        covered = functools.reduce(
            operator.or_,
            (1 << index for index, slot in enumerate(self.slots) if slot.code in codes),
            self.forced,
        )
        return self.everything if covered & self.reads_code else covered

    def digest(self, covered: int) -> str:
        """Digest (hex) with the code contents for covered functions and the marker :data:`_UNCOVERED` for the others."""
        known = self.digests.get(covered)
        if known is not None:
            return known
        if self.strict is not None and covered & self.reads_code:
            # When a covered function reads a function's contents as a value, cover the contents of all functions (:meth:`covered_by`) — the digest of the per-annotation-read walk.
            return self.digests.setdefault(covered, self.strict.digest(self.strict.everything))
        pieces = [self.segments[0]]
        for index, slot in enumerate(self.slots):
            text = (slot.shape if covered >> index & 1 else _UNCOVERED).encode("utf-8")
            pieces.extend((_LENGTH.pack(len(text)), text, self.segments[index + 1]))
        return self.digests.setdefault(covered, hashlib.sha256(b"".join(pieces)).hexdigest())


@dataclass(frozen=True)
class FileState:
    """File state that decides whether a remembered digest is still valid (path, modification time, size)."""

    path: str
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class RememberedDigest:
    """One in-process digest memory — used only when the files of the owners of what was walked are still in the same state and it is for the same function (or module)."""

    subject: object
    files: tuple[FileState, ...]
    closure: ClosureDigest


#: In-process memory of digests (so that the closure of the same macro is not walked again at every verification).
_REMEMBERED: dict[UsedMacroKey | ValueReference, RememberedDigest] = {}


def macro_digest(key: UsedMacroKey, macro: FunctionType, sha256_of: Sha256Of) -> ClosureDigest:
    """Digest of the closure of a macro function's code (the remembered one if there is one)."""
    return _remembered(key, macro, lambda walker: walker.function(macro), sha256_of)


def value_digest(reference: ValueReference, sha256_of: Sha256Of) -> ClosureDigest | None:
    """Digest of the closure of a value (a module attribute) outside the macro that changes the expansion result (imports the module — None if it cannot be looked up)."""
    module = _imported(reference.module)
    if module is None or reference.name not in vars(module):
        return None
    value = vars(module)[reference.name]
    return _remembered(
        reference, value, lambda walker: walker.outside_value(module, value), sha256_of
    )


def _remembered(
    key: UsedMacroKey | ValueReference,
    subject: object,
    walk: Callable[["_Walker"], None],
    sha256_of: Sha256Of,
) -> ClosureDigest:
    """The remembered digest, or the digest made by walking (so the closure is not walked again each time the same macro is verified)."""
    remembered = _REMEMBERED.get(key)
    if remembered is not None and remembered.subject is subject and _confirmed(key, remembered):
        return remembered.closure
    entry = _facts_entry(key)
    stored = _stored_facts(entry) if entry is not None else {}
    walker = _Walker(sha256_of, CodeReadings(stored))
    walk(walker)
    walked = walker.walked()  # finish adding the attributes named by string constants (here too a module in the middle of an import may be hit)
    walkers = [walker]
    if walked.reads_code:
        # A function that reads a function's contents as a value is in the closure: walk again with per-annotation reads, and build here the digest for when that function is among the covered functions.
        strict_walker = _Walker(sha256_of, CodeReadings(stored, strict=True))
        walk(strict_walker)
        walked = dataclasses.replace(walked, strict=strict_walker.walked())
        walkers.append(strict_walker)
    if entry is not None and any(each.readings.missed for each in walkers):
        # so that the next process's walk of the same macro does not re-read the code
        _write_facts(entry, {sha: facts for each in walkers for sha, facts in each.readings.used.items()})
    modules = tuple(sorted({name for each in walkers for name in (*each.files, *each.unimportable)}))
    if any(each._mut_environment for each in walkers):
        # A digest that hit an environment variable read does not capture the environment variable values — mark it unverifiable and do not remember it. Decide this before the in-the-middle-of-import state
        # (rows in an in-the-middle state are compared by file and would not notice a difference in environment variables).
        return ClosureDigest(ENVIRONMENT_READ, modules)
    if any(each._mut_partial for each in walkers):
        # Do not remember a digest from an in-the-middle-of-import state — if remembered, this process's verifications and records would all take that value, and
        # processes with a different import order would keep overwriting each other's entries in storage.
        return ClosureDigest(None, modules)
    closure = ClosureDigest(walked.digest(walked.everything), modules, walked, walked.everything)
    states = tuple(
        state
        for path in sorted({path for each in walkers for path in each.files.values()})
        if (state := _state_now(path)) is not None
    )
    _REMEMBERED[key] = RememberedDigest(subject, states, closure)
    return closure


def _confirmed(key: UsedMacroKey | ValueReference, remembered: RememberedDigest) -> bool:
    """Whether a remembered digest may be used — whether the files it depended on are still in the state they were in when remembered. Within one outermost load the file state is treated as
    unchanging, so a remembered entry already checked is not checked twice (verification runs per Hy module, and used to check the same entry hundreds of times)."""
    scope = current_scope()
    if scope is not None and scope.confirmed.get(key) is remembered:
        return True
    if not all(map(_unchanged, remembered.files)):
        return False
    if scope is not None:
        scope.confirmed[key] = remembered
    return True


def _file_state(path: str) -> FileState | None:
    """Current state of a file (decides whether a remembered digest may be used — a file that has disappeared is None)."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return FileState(path, status.st_mtime_ns, status.st_size)


#: Marker in the outermost load's memory for a file whose state has not been read yet (distinct from None for a file that has disappeared).
_UNREAD = object()


def _state_now(path: str) -> FileState | None:
    """Current state of a file, used for checking memory and for remembering — within one outermost load (:mod:`doeff_hy_bytecode_guard._import_scope`)
    each file is stat-ed only once (a check load used to run 797 verifications and 9,700 stats). When remembering
    outside a load (the tool that makes records), it stats every time."""
    scope = current_scope()
    if scope is None:
        return _file_state(path)
    known = scope.states.get(path, _UNREAD)
    if known is _UNREAD:
        known = scope.states[path] = _file_state(path)
    return known if isinstance(known, FileState) else None


def _unchanged(state: FileState) -> bool:
    """Whether the file is still in the state it was in when remembered (a file that has disappeared is false) — the same judgment as :func:`_file_state`."""
    now = _state_now(state.path)
    return now is not None and now.mtime_ns == state.mtime_ns and now.size == state.size


def _closure_files(names: frozenset[str], sha256_of: Sha256Of) -> tuple[UsedFile, ...]:
    """sha256 of the files of the modules the closure depended on — what verification compares without importing when there are rows that cannot be compared by digest (the record or the current walk
    hit a module in the middle of an import). The files of modules that could not be imported are included too (a file that cannot be looked up gets a marker that differs every time — always
    judged stale)."""
    return tuple(
        UsedFile(name, (sha256_of(path) if path is not None else None) or os.urandom(16).hex())
        for name in sorted(names)
        if not _named_only(name)
        for path in (_module_file(name),)
    )


def _module_file(module_name: str) -> str | None:
    """The file of a module name in the current environment — sys.modules if already loaded, otherwise an import search (executing nothing, including the parent packages —
    ``importlib.util.find_spec`` imports the parent package, whose body loads other modules)."""
    module = sys.modules.get(module_name)
    if module is not None:
        file = vars(module).get("__file__")
        return file if isinstance(file, str) else None
    spec = _spec_without_import(module_name)
    origin = None if spec is None else spec.origin
    return origin if isinstance(origin, str) and os.path.isfile(origin) else None


def _spec_without_import(module_name: str) -> importlib.machinery.ModuleSpec | None:
    """Ask the import search finders directly for a module's spec (the parent package's ``__path__`` if loaded, otherwise the same searched
    location — nothing is executed)."""
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        spec = vars(loaded).get("__spec__")
        return spec if isinstance(spec, importlib.machinery.ModuleSpec) else None
    parent, _, _ = module_name.rpartition(".")
    locations: list[str] | None = None
    if parent:
        parent_spec = _spec_without_import(parent)
        found = None if parent_spec is None else parent_spec.submodule_search_locations
        if found is None:
            return None
        locations = list(found)
    for finder in sys.meta_path:
        spec = finder.find_spec(module_name, locations, None)
        if spec is not None:
            return spec
    return None


# ---- Looking up macros from the names collected -------------------------------------------------------------------------------------


def _from_elsewhere(macro: FunctionType, module_name: str) -> bool:
    """Whether the macro was defined outside the module being expanded (its own macros are covered by the source; Hy's own macros are covered by the Hy version)."""
    owner = macro.__module__
    return isinstance(owner, str) and owner != module_name and not _named_only(owner)


def _macro_key(table: str, macro: FunctionType) -> UsedMacroKey:
    """The name within the table of the module that defined the macro (the name that looks up the same function object — the defined name, not a require alias)."""
    owner = str(macro.__module__)
    module = sys.modules.get(owner)
    entries = None if module is None else vars(module).get(table)
    if isinstance(entries, dict):
        for name in sorted(entries):
            if dict.__getitem__(entries, name) is macro:
                return UsedMacroKey(table, owner, name)
    return UsedMacroKey(table, owner, macro.__name__)


def _macro_now(table: str, module_name: str, name: str) -> FunctionType | None:
    """The macro looked up from a module name in the current environment (imports the module — None if it cannot be looked up)."""
    module = _imported(module_name)
    entries = None if module is None else vars(module).get(table)
    if not isinstance(entries, dict) or name not in entries:
        return None
    macro = dict.__getitem__(entries, name)
    return macro if isinstance(macro, FunctionType) else None


@dataclass(frozen=True)
class NotImportable:
    """A module that could not be imported for the digest (the name, and the Hy or import failure text)."""

    module: str
    reason: str


def _import_for_digest(module_name: str) -> ModuleType | NotImportable:
    """Import a module for the digest. Verification runs in the middle of reading a .pyc (in the middle of an import), so an import target inside a function
    may require a module in the middle of a circular import and fail (expansion does not take that branch, so it does not fail) — on failure return a value."""
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    from hy.errors import HyError  # verification runs only when it hits a Hy module

    try:
        return importlib.import_module(module_name)
    except (ImportError, HyError) as error:
        return NotImportable(module_name, f"{type(error).__name__}: {error}")


def _imported(module_name: str) -> ModuleType | None:
    """The module for a module name (imports it if not loaded — None if it cannot be imported. The verifying side judges stale)."""
    match _import_for_digest(module_name):
        case ModuleType() as module:
            return module
        case NotImportable():
            return None


@dataclass(frozen=True)
class MacroReference:
    """The macro that a ``hy.R.<module>.<name>`` call looks up (the module name and the mangled name)."""

    module: str
    name: str


#: ``hy.R.<module>.<name>`` within a source statement (/ in the module part stands in for .).
_HY_R = re.compile(r"hy\.R\.([^\s.()\[\]{}\"';]+)\.([^\s()\[\]{}\"';]+)")


def _source_references(text: str) -> Iterator[MacroReference]:
    """hy.R calls written directly in a source statement (annotation lines are not read)."""
    for line in text.splitlines():
        if line.lstrip().startswith(";") or "hy.R." not in line:
            continue
        for match in _HY_R.finditer(line):
            yield _reference(match.group(1), match.group(2))


def _reference(module_part: str, name_part: str) -> MacroReference:
    """Build the macro location from the module part and name part of hy.R, reading them the same way as Hy's macroexpand."""
    return MacroReference(
        _reference_module(module_part), ".".join(map(_mangle, name_part.split(".")))
    )


def _reference_module(module_part: str) -> str:
    """The module name of the module part of hy.R (``doeff_hy/record``)."""
    from hy.reader.mangling import slashes2dots

    return slashes2dots(_mangle(module_part))


def _mangle(text: str) -> str:
    """Hy's name mangling (Hy is already loaded when a record is made and when it is verified)."""
    import hy

    return hy.mangle(text)


# ---- Digest of the closure of code ------------------------------------------------------------------------------------------


#: Packages contributing only their name (covered by the Hy version and the Python version). The standard library is added from sys.stdlib_module_names.
#: doeff_hy_bytecode_guard is the way records are made itself (the record shape is covered by RECORD_TAG; it holds in-process memory).
_NAMED_ONLY = frozenset({"hy", "builtins", "funcparserlib", "doeff_hy_bytecode_guard"})


def _named_only(module_name: str) -> bool:
    """Whether it is a module contributing only its name (Hy itself, the standard library, builtins — covered by the version)."""
    top = module_name.partition(".")[0]
    return top in _NAMED_ONLY or top in sys.stdlib_module_names


#: Standard modules holding environment variables (os and below it posix and nt), and the attribute names that read environment variables (the tables environ and environb, and the getenv and
#: getenvb that read them) — among the name-only modules, reads of process state that the version cannot cover (the "environment variable reads" in the module docstring).
_ENVIRONMENT_MODULES = frozenset({"os", "posix", "nt"})
_ENVIRONMENT_NAMES = frozenset({"environ", "environb", "getenv", "getenvb"})


def _reads_environment(namespace: Mapping[str, object], names: tuple[str, ...]) -> bool:
    """Whether code or a binding statement looks up a module holding environment variables by a global name and reads environment variables — to identify unverifiable digests. When the name list
    has an attribute name that reads environment variables and any name in the list points to that module as a global (``os.environ.get``, or ``o.getenv`` for ``(import os :as o)``).
    The name list includes attribute names too, so if an attribute ``x.environ`` of another object and a reference to os are in the same code, it counts as a read
    (the safe side — the users of that macro just become unverifiable)."""
    if _ENVIRONMENT_NAMES.isdisjoint(names):
        return False
    return any(_environment_module(namespace.get(name)) for name in names)


def _source_reads_environment(names: tuple[str, ...]) -> bool:
    """Whether all the names appearing in a module's source could include an environment variable read — to decide, when covering an unwalkable value by its owner's file, whether to
    make it an unverifiable marker. When both the name of a module holding environment variables and the name of an attribute that reads environment variables appear (where it reads — at load time
    or when called — is not distinguished. The safe side)."""
    return not (_ENVIRONMENT_NAMES.isdisjoint(names) or _ENVIRONMENT_MODULES.isdisjoint(names))


def _environment_module(value: object) -> bool:
    """Whether the value is a standard module holding environment variables (an os imported under another name is the same module object)."""
    return isinstance(value, ModuleType) and value.__name__ in _ENVIRONMENT_MODULES


def _environment_object(value: object) -> bool:
    """Whether the value is itself an object that reads environment variables (os.environ, os.environb, os.getenv, os.getenvb, or the environ of posix or nt) — to identify reads that were met as
    a value without looking up the module by name (an aliased import, a default, a closure cell)."""
    return any(
        value is found
        for module in _ENVIRONMENT_MODULES
        if (owner := sys.modules.get(module)) is not None
        for name in _ENVIRONMENT_NAMES
        if (found := vars(owner).get(name)) is not None
    )


class NamespaceRead(enum.Enum):
    """Whether code has reads that cannot be followed by name, and if so how broad.

    - NONE = it has none.
    - OBJECT = a read of the namespace of a passed object (``x.__dict__``, ``__getattribute__``, ``attrgetter``, ``getattr_static``).
      The names read are covered as string constants of the code, as with ``getattr(x, "name")`` (:attr:`CodeFacts.texts`). The function's own module cannot be passed as an object
      without a MODULE read (``globals()``, ``sys.modules``, ``import_module`` ...), so it is covered only by the files of the
      modules the function references.
    - MODULE = a read of a module namespace, a module table, or a string expression (``globals()``, ``vars()``, ``sys.modules``, ``eval`` ...) —
      covered by the function's module and the files of the modules the function references.

    ``vars(x)`` called with one argument read as a plain value is ``x.__dict__`` (OBJECT); only ``vars()`` with no argument, and every other use of ``vars`` (passed as a value,
    called with a computed argument, ``*`` or a keyword), is MODULE (:func:`_one_plain_argument`).
    """

    NONE = "none"
    OBJECT = "object"
    MODULE = "module"


#: Names that become a MODULE read when read as a global name (``vars`` only when called as a global builtin — a field name ``x.vars`` is not a read; ``vars(x)`` with one
#: plain argument is an OBJECT read — :data:`_OBJECT_READ_CALLS`).
_MODULE_READ_GLOBALS = frozenset(
    {
        "globals",
        "vars",
        "locals",
        "eval",
        "exec",
        "__import__",
        "import_module",
        "modules",
        "macroexpand",
        "macroexpand_1",
    }
)

#: Names that become a MODULE read when read as an attribute (``sys.modules``, ``hy.eval``, ``importlib.import_module``, ``f.__globals__``).
_MODULE_READ_ATTRIBUTES = frozenset(
    {
        "eval",
        "exec",
        "__import__",
        "import_module",
        "modules",
        "macroexpand",
        "macroexpand_1",
        "__globals__",
    }
)

#: Global names that are an OBJECT read when called with one plain argument (``vars(cls)`` reads ``cls.__dict__`` — the record functions of defrecord types read
#: ``vars(cls)``; as MODULE they covered _record_runtime by its file, and a docstring rewrite there made every defhandler user stale).
_OBJECT_READ_CALLS = frozenset({"vars"})

#: Names that become an OBJECT read when read as a global name or an attribute.
_OBJECT_READ_GLOBALS = frozenset({"attrgetter", "getattr_static"})
_OBJECT_READ_ATTRIBUTES = frozenset(
    {"__dict__", "__getattribute__", "attrgetter", "getattr_static"}
)

_UNTRACEABLE_NAMES = (
    _MODULE_READ_GLOBALS | _MODULE_READ_ATTRIBUTES | _OBJECT_READ_GLOBALS | _OBJECT_READ_ATTRIBUTES
)


#: Parts of a class's contents that are not behavior (line numbers, annotations, internal memory of abstract classes, dataclass field descriptions).
_CLASS_SKIP = frozenset(
    {
        "__dict__",
        "__weakref__",
        "__module__",
        "__qualname__",
        "__firstlineno__",
        "__static_attributes__",
        "__annotations__",
        "__annotate__",
        "__annotate_func__",
        "__annotations_cache__",
        "__orig_bases__",
        "__parameters__",
        "__type_params__",
        "__dataclass_fields__",
        "__dataclass_params__",
        "_abc_impl",
        # Tables that enum and namedtuple build from a class definition (members and field defaults are followed as members and the defaults of __new__).
        "_member_map_",
        "_value2member_map_",
        "_member_names_",
        "_hashable_values_",
        "_unhashable_values_",
        "_unhashable_values_map_",
        "_member_type_",
        "_new_member_",
        "_use_args_",
        "_value_repr_",
        "__classdictcell__",
        "_field_defaults",
    }
)


@dataclass(frozen=True)
class ImportReference:
    """One import instruction in code (the module name and the relative level)."""

    name: str
    level: int


@dataclass(frozen=True)
class CodeFacts:
    """What was read from one code object (including nested code) — the digest of its contents without line numbers and positions, the name table, string constants
    (candidate names for ``getattr(x, "name")`` and ``d["name"]``), imports, the modules of hy.R calls, whether it has reads that cannot be followed by name, and whether it reads a module's
    location (:data:`_LOCATION_NAMES`) as a value (a global read, an attribute read, or a string constant name — false if it only writes)."""

    shape: str
    names: tuple[str, ...]
    texts: tuple[str, ...]
    imports: tuple[ImportReference, ...]
    called_modules: tuple[str, ...]
    reads: NamespaceRead
    reads_location: bool


#: In-process memory of code reads (code object id -> (code, what was read) — keeps the code alive to prevent reuse of ids).
#: Reads with the things that do not affect meaning removed (function type annotations, the numbers in the names of Hy temporary variables).
_FACTS: dict[int, tuple[CodeType, CodeFacts]] = {}

#: Memory of per-annotation reads (``strict`` of :func:`_code_facts`). Used only by the walk of a closure that reads annotations as values (:data:`_CODE_READ_NAMES`).
_STRICT_FACTS: dict[int, tuple[CodeType, CodeFacts]] = {}


def _code_facts(code: CodeType, strict: bool = False) -> CodeFacts:
    """Read from a code object what the digest needs (excluding line numbers and positions — read only once per code within the process).

    A read with ``strict`` false excludes two things that do not affect the expansion result (a change that only added a type annotation, or that only shifted Hy temporary variable numbers because a function was added earlier,
    used to change the digest of the macro's closure and make all users' modules expand again):

    - Function type annotations (the nested code of 3.14's ``__annotate__`` and the instruction that attaches it to the function — :func:`_function_annotations`. For 3.13 and earlier, the annotation tuple
      pushed before creating the function and the flag that attaches it — :func:`_inline_function_annotations`). Names and imports that appear only inside an annotation
      are also left out of that code's reads (name table, strings, imports ...). The annotations in a class body (what binds ``__annotate_func__`` — dataclass fields) are not excluded.
    - The numbers in the names of Hy temporary variables (``_hy_anon_46`` -> ``_hy_anon_48``). Per code object, renumbered to sequence numbers in order of appearance of instructions

    A read with ``strict`` true includes annotations too (the renumbering is the same)."""
    cache = _STRICT_FACTS if strict else _FACTS
    remembered = cache.get(id(code))
    if remembered is not None and remembered[0] is code:
        return remembered[1]
    instructions = _Instructions(code, drop_annotations=not strict)
    nested = tuple(
        _code_facts(constant, strict)
        for constant in instructions.constants
        if isinstance(constant, CodeType)
    )
    texts = tuple(
        constant for constant in instructions.constants if isinstance(constant, str)
    )  # keeps the order of the constants
    facts = CodeFacts(
        shape=instructions.shape(strict),
        names=tuple(sorted({*instructions.names, *(name for inner in nested for name in inner.names)})),
        texts=tuple(sorted({*texts, *(text for inner in nested for text in inner.texts)})),
        imports=(*_imports(code), *(i for inner in nested for i in inner.imports)),
        called_modules=tuple(
            sorted(
                {*_called_modules(texts), *(m for inner in nested for m in inner.called_modules)}
            )
        ),
        reads=_widest((_namespace_read(code), *(inner.reads for inner in nested))),
        reads_location=_reads_location(code, texts) or any(inner.reads_location for inner in nested),
    )
    cache[id(code)] = (code, facts)
    return facts


def _reads_location(code: CodeType, texts: tuple[str, ...]) -> bool:
    """Whether code (excluding nested code) reads a module's location as a value — a global read (``__file__``), an attribute read (``m.__file__``), or a string
    constant name (``getattr(m, "__file__")``). Merely writing to a module you made (``module.__file__ = ...`` — STORE_ATTR) is not a read."""
    if not _LOCATION_NAMES.isdisjoint(texts):
        return True
    if _LOCATION_NAMES.isdisjoint(code.co_names):
        return False
    return any(
        use.name in _LOCATION_NAMES and use.way is not NameWay.OTHER for use in _name_uses(code)
    )


def _constant_text(value: object, strict: bool) -> str:
    """The text of one constant of code (nested code is a digest without line numbers; sets take a form independent of order)."""
    match value:
        case CodeType():
            return f"code:{_code_facts(value, strict).shape}"
        case tuple():
            return "(" + ",".join(_constant_text(item, strict) for item in value) + ")"
        case frozenset():
            return "{" + ",".join(sorted(_constant_text(item, strict) for item in value)) + "}"
        case _:
            # A Hy temporary name inside a string constant (prefix + ``..._N``) is a value, not a name (not renumbered — to make it a form that :func:`_numbered_anon_names` does not pick up,
            # a NUL is inserted before the last ``_`` of the prefix).
            return f"{type(value).__name__}:{value!r}".replace(
                GENERATED_NAME_PREFIX, GENERATED_NAME_PREFIX[:-1] + "\0_"
            )


#: Instruction that attaches annotations to a function (the flags of 3.14's ``SET_FUNCTION_ATTRIBUTE`` — 0x10 = ``__annotate__``, 0x08 = closure).
_ANNOTATE_ATTRIBUTE = 0x10
_CLOSURE_ATTRIBUTE = 0x08
#: The flag that attaches an annotation tuple to a function in 3.13 and earlier (3.13's ``SET_FUNCTION_ATTRIBUTE``, ``MAKE_FUNCTION`` in 3.11 and 3.12).
_ANNOTATIONS_ATTRIBUTE = 0x04
#: Instructions that push cells before building the closure tuple (one that pushes one and one that pushes two).
_CELL_LOADS = frozenset({"LOAD_FAST", "LOAD_FAST_BORROW", "LOAD_FAST_CHECK", "LOAD_DEREF"})
_CELL_PAIR_LOADS = frozenset({"LOAD_FAST_LOAD_FAST", "LOAD_FAST_BORROW_LOAD_FAST_BORROW"})
#: The class of instructions whose argument is counted not as an index but replaced by the name, constant, or jump target the index points to (because indexes change with the order of the constant table and name table).
_NAMED_OPCODES = frozenset((*dis.hasname, *dis.haslocal, *dis.hasfree))
#: The class of instructions that point to a name in the name table (``co_names``).
_NAME_OPCODES = frozenset(dis.hasname)
_JUMP_OPCODES = frozenset(dis.hasjump) if hasattr(dis, "hasjump") else frozenset((*dis.hasjrel, *dis.hasjabs))
_CONST_OPCODES = frozenset(dis.hasconst)


def _function_annotations(instructions: list[dis.Instruction]) -> tuple[set[int], list[CodeType]]:
    """List, from a sequence of instructions, the positions of the instructions that attach a type annotation to a function and the code of that ``__annotate__``.

    In 3.14, a function's annotations are placed before the construction of the function body as the instructions that make the annotation function (the instruction that pushes cells and makes a tuple if a closure is needed,
    ``LOAD_CONST`` of the code, ``MAKE_FUNCTION``, and for a closure ``SET_FUNCTION_ATTRIBUTE`` of the closure), and after the function is made they are attached by ``SET_FUNCTION_ATTRIBUTE``
    of ``__annotate__``. Only a shape that fits this exactly is listed; shapes that do not fit (what binds ``__annotate_func__`` in a class body,
    functions with type parameters, sequences that cannot be read) are not listed — not listing only means the annotation goes into the digest whole, which never errs toward judging an old expansion as matching."""
    dropped: set[int] = set()
    codes: list[CodeType] = []
    count = len(instructions)
    for index, instruction in enumerate(instructions):
        if (
            instruction.opname != "LOAD_CONST"
            or not isinstance(instruction.argval, CodeType)
            or instruction.argval.co_name != "__annotate__"
        ):
            continue
        end = index + 1
        if end >= count or instructions[end].opname != "MAKE_FUNCTION":
            continue
        end += 1
        start = index
        if end < count and _sets_attribute(instructions[end], _CLOSURE_ATTRIBUTE):
            end += 1
            start = _closure_tuple_start(instructions, index)
            if start < 0:
                continue
        attach = _annotate_attachment(instructions, end)
        if attach < 0:
            continue
        dropped.update(range(start, end))
        dropped.add(attach)
        codes.append(instruction.argval)
    return dropped, codes


def _sets_attribute(instruction: dis.Instruction, flag: int) -> bool:
    """Whether the instruction is a ``SET_FUNCTION_ATTRIBUTE`` that attaches the function attribute ``flag``."""
    return instruction.opname == "SET_FUNCTION_ATTRIBUTE" and instruction.arg == flag


def _closure_tuple_start(instructions: list[dis.Instruction], at: int) -> int:
    """Start of the instructions that build the closure tuple pushed by the instruction at position ``at`` (``BUILD_TUPLE n``, and the instructions that push n cells. -1 if it cannot be read)."""
    last = at - 1
    if last < 0 or instructions[last].opname != "BUILD_TUPLE" or not isinstance(
        instructions[last].arg, int
    ):
        return -1
    wanted = instructions[last].arg
    taken = 0
    position = last - 1
    while taken < wanted:
        if position < 0:
            return -1
        name = instructions[position].opname
        if name in _CELL_LOADS:
            taken += 1
        elif name in _CELL_PAIR_LOADS:
            taken += 2
        else:
            return -1
        position -= 1
    return position + 1 if taken == wanted else -1


def _annotate_attachment(instructions: list[dis.Instruction], start: int) -> int:
    """From after the annotation function was pushed (position ``start``), read the function construction that attaches it (closure tuple, the code ``LOAD_CONST``, ``MAKE_FUNCTION``,
    the closure ``SET_FUNCTION_ATTRIBUTE``) and return the position of the following ``__annotate__`` ``SET_FUNCTION_ATTRIBUTE`` (-1 if it does not fit)."""
    count = len(instructions)
    position = start
    while position < count and instructions[position].opname in (
        *_CELL_LOADS,
        *_CELL_PAIR_LOADS,
        "BUILD_TUPLE",
    ):
        position += 1
    if (
        position + 1 >= count
        or instructions[position].opname != "LOAD_CONST"
        or not isinstance(instructions[position].argval, CodeType)
        or instructions[position + 1].opname != "MAKE_FUNCTION"
    ):
        return -1
    position += 2
    if position < count and _sets_attribute(instructions[position], _CLOSURE_ATTRIBUTE):
        position += 1
    if position < count and _sets_attribute(instructions[position], _ANNOTATE_ATTRIBUTE):
        return position
    return -1


def _inline_function_annotations(
    instructions: list[dis.Instruction],
) -> tuple[set[int], dict[int, dis.Instruction]]:
    """List, from a sequence of instructions, the positions of the instructions that attach a type annotation to a function in the form up to 3.13, and the ``MAKE_FUNCTION`` to be counted with the flag removed.

    Up to 3.13, a function's annotations are pushed alternately as a name (string constant) and an annotation expression, made into a tuple by ``BUILD_TUPLE``, and pushed before the function body code
    (before the closure tuple if there is one). 3.13 attaches it after making the function with the flag 0x04 of ``SET_FUNCTION_ATTRIBUTE``, and 3.11 and 3.12 attach it with the flag 0x04 of
    ``MAKE_FUNCTION``. The tuple push is read through the instructions' stack effects, and only a tuple whose names are all string constants is listed
    (shapes that do not fit — conditional expressions or calls inside an annotation — are not listed, and the annotation just goes into the digest whole)."""
    dropped: set[int] = set()
    unflagged: dict[int, dis.Instruction] = {}
    for index, instruction in enumerate(instructions):
        if (
            instruction.opname != "MAKE_FUNCTION"
            or index == 0
            or instructions[index - 1].opname != "LOAD_CONST"
            or not isinstance(instructions[index - 1].argval, CodeType)
        ):
            continue
        attributes = _attribute_run(instructions, index + 1)
        flags = (
            functools.reduce(operator.or_, (instructions[position].arg or 0 for position in attributes), 0)
            if attributes
            else instruction.arg or 0
        )
        if not flags & _ANNOTATIONS_ATTRIBUTE:
            continue
        end = index - 2
        if flags & _CLOSURE_ATTRIBUTE:
            end = _value_start(instructions, end) - 1
        start = _value_start(instructions, end)
        if end < 0 or start < 0 or not _is_annotation_pairs(instructions, start, end):
            continue
        dropped.update(range(start, end + 1))
        attach = [position for position in attributes if instructions[position].arg == _ANNOTATIONS_ATTRIBUTE]
        if attributes:
            dropped.update(attach)
        else:
            unflagged[index] = instruction._replace(arg=flags & ~_ANNOTATIONS_ATTRIBUTE)
    return dropped, unflagged


def _attribute_run(instructions: list[dis.Instruction], start: int) -> range:
    """Positions of the run of ``SET_FUNCTION_ATTRIBUTE`` following position ``start`` (empty up to 3.12)."""
    end = next(
        (
            position
            for position in range(start, len(instructions))
            if instructions[position].opname != "SET_FUNCTION_ATTRIBUTE"
        ),
        len(instructions),
    )
    return range(start, end)


def _value_start(instructions: list[dis.Instruction], end: int) -> int:
    """Start of the run of instructions that ends at position ``end`` and pushes exactly one value (stack effects are summed from the back. -1 if it contains a jump or cannot be read)."""
    total = 0
    for position in range(end, -1, -1):
        instruction = instructions[position]
        if instruction.opcode in _JUMP_OPCODES:
            return -1
        try:
            total += dis.stack_effect(
                instruction.opcode, instruction.arg if instruction.opcode >= dis.HAVE_ARGUMENT else None
            )
        except ValueError:
            return -1
        if total == 1:
            return position
        if total > 1:
            return -1
    return -1


def _is_annotation_pairs(instructions: list[dis.Instruction], start: int, end: int) -> bool:
    """Whether the instructions from position ``start`` to ``end`` push a tuple of alternating names (string constants) and annotations (including the form of a single folded constant tuple)."""
    last = instructions[end]
    if start == end:
        value = last.argval
        return (
            last.opname == "LOAD_CONST"
            and isinstance(value, tuple)
            and len(value) % 2 == 0
            and all(isinstance(name, str) for name in value[::2])
        )
    if last.opname != "BUILD_TUPLE" or not isinstance(last.arg, int) or last.arg % 2:
        return False
    starts = _value_starts(instructions, end - 1, last.arg)
    return (
        starts is not None
        and starts[-1] == start
        and all(  # counted from the back, so the odd-numbered ones are names
            instructions[at].opname == "LOAD_CONST" and isinstance(instructions[at].argval, str)
            for at in starts[1::2]
        )
    )


def _value_starts(instructions: list[dis.Instruction], end: int, count: int) -> tuple[int, ...] | None:
    """Start of a run of ``count`` instructions that each push one value, lined up backward from position ``end`` (counted from the back. None if it cannot be read)."""
    if count == 0:
        return ()
    found = _value_start(instructions, end)
    rest = None if found < 0 else _value_starts(instructions, found - 1, count - 1)
    return None if rest is None else (found, *rest)


class _Instructions:
    """The instruction sequence of one code object (excluding nested code) and the digest of its contents with the things that do not affect meaning removed (:func:`_code_facts`)."""

    def __init__(self, code: CodeType, drop_annotations: bool) -> None:
        self._code = code
        listed = [
            instruction
            for instruction in dis.get_instructions(code, adaptive=False)
            if instruction.opname != "EXTENDED_ARG"
        ]
        dropped, self._annotations = (
            _function_annotations(listed) if drop_annotations else (set(), [])
        )
        inline, unflagged = _inline_function_annotations(listed) if drop_annotations else (set(), {})
        dropped |= inline
        self._kept = [
            unflagged.get(index, instruction)
            for index, instruction in enumerate(listed)
            if index not in dropped
        ]
        removed = [listed[index] for index in dropped]
        # Names and constants pointed to only by the removed instructions (annotation names and argument-name strings up to 3.13) are also removed from the reads of the name table and constant table.
        kept_names = {instruction.argval for instruction in self._kept if instruction.opcode in _NAME_OPCODES}
        self._removed_names = {
            instruction.argval for instruction in removed if instruction.opcode in _NAME_OPCODES
        } - kept_names
        kept_constants = {instruction.arg for instruction in self._kept if instruction.opcode in _CONST_OPCODES}
        self._removed_constants = {
            instruction.arg for instruction in removed if instruction.opcode in _CONST_OPCODES
        } - kept_constants

    @property
    def names(self) -> tuple[str, ...]:
        """The name table with the names pointed to only by the removed instructions taken out (in table order)."""
        return tuple(name for name in self._code.co_names if name not in self._removed_names)

    @property
    def constants(self) -> list[object]:
        """The constant table with the removed annotation code and the constants pointed to only by the removed instructions taken out (in table order)."""
        return [
            constant
            for index, constant in enumerate(self._code.co_consts)
            if index not in self._removed_constants
            and not (isinstance(constant, CodeType) and self.dropped(constant))
        ]

    def dropped(self, constant: CodeType) -> bool:
        """Whether the constant code is a removed annotation code."""
        return any(constant is annotation for annotation in self._annotations)

    def shape(self, strict: bool) -> str:
        """Digest of the contents without line numbers and positions. Instructions are counted not by index but by the name, constant, or jump-target instruction they point to (unchanged even when the order of the constant table and
        name table changes), the removed annotation instructions and code are not included, and the numbers in Hy temporary variable names are renumbered in order of appearance."""
        code = self._code
        offsets = [instruction.offset for instruction in self._kept]
        lines = [self._line(instruction, offsets, strict) for instruction in self._kept]
        handlers = [
            ",".join(
                (
                    str(bisect.bisect_left(offsets, start)),
                    str(bisect.bisect_left(offsets, end)),
                    str(bisect.bisect_left(offsets, target)),
                    str(depth),
                    str(lasti),
                )
            )
            for start, end, target, depth, lasti in _exception_table(code)
        ]
        # Keep the order of the constant table (the docstring is not pointed to by an instruction; only the position at the head of the table is the marker) — closed up by the removed annotation code and constants.
        constants = [_constant_text(constant, strict) for constant in self.constants]
        document = "\0".join(
            (
                code.co_name,
                code.co_qualname,
                str(code.co_argcount),
                str(code.co_posonlyargcount),
                str(code.co_kwonlyargcount),
                str(code.co_flags),
                "\n".join(lines),
                "\n".join(handlers),
                repr(self.names),
                repr(code.co_varnames),
                repr(code.co_freevars),
                repr(code.co_cellvars),
                *constants,
            )
        )
        return hashlib.sha256(
            _numbered_anon_names(document).encode("utf-8", "surrogatepass")
        ).hexdigest()

    @staticmethod
    def _line(instruction: dis.Instruction, offsets: list[int], strict: bool) -> str:
        """The text of one instruction (name and argument — counted by what the index points to)."""
        opcode = instruction.opcode
        if opcode in _JUMP_OPCODES:
            argument = f"@{bisect.bisect_left(offsets, instruction.argval)}"
        elif opcode in _CONST_OPCODES:
            argument = _constant_text(instruction.argval, strict)
        elif opcode in _NAMED_OPCODES:
            argument = instruction.argrepr
        else:
            argument = str(instruction.arg)
        return f"{instruction.opname} {argument}"


def _exception_table(code: CodeType) -> Iterator[tuple[int, int, int, int, bool]]:
    """Rows of the code's exception table (start, end, handler position, depth, lasti. Positions are byte offsets — the same varint as ``dis``'s private reader)."""
    iterator = iter(code.co_exceptiontable)

    def varint() -> int:
        byte = next(iterator)
        value = byte & 63
        while byte & 64:
            value <<= 6
            byte = next(iterator)
            value |= byte & 63
        return value

    try:
        while True:
            start = varint() * 2
            end = start + varint() * 2
            target = varint() * 2
            depth_and_lasti = varint()
            yield start, end, target, depth_and_lasti >> 1, bool(depth_and_lasti & 1)
    except StopIteration:
        return


def _numbered_anon_names(document: str) -> str:
    """Renumber the numbers of Hy temporary variable names in the text (:data:`doeff_hy_bytecode_guard._records.ANON_VAR_NAME` — ``_hy_anon_46``, ``_hy_anon_fn_12``) into
    sequence numbers in order of appearance in the text. Hy numbers temporary variables with the module's running count, so just one more function earlier shifts the numbers of the functions after it.
    The same name gets the same number and different names get different numbers (one to one — the identity of variables within the code is preserved)."""
    ranks: dict[str, str] = {}

    def renumber(found: re.Match[str]) -> str:
        name = found.group(0)
        if name not in ranks:
            ranks[name] = f"{found.group(1)}_#{len(ranks) + 1}"
        return ranks[name]

    return ANON_VAR_NAME.sub(renumber, document)


# ---- Remembering code reads across processes -------------------------------------
#
# A code read (:class:`CodeFacts`) is a pure computation determined only by the contents of the code object and the way of reading (this module's code, the Python version, the Hy
# mangle version that builds the hy.R module names), so
# it is looked up and remembered in Hy's code storage (code_store kind FACTS_SUFFIX) by the code contents (sha256 of the marshal bytes) — a new process's
# verification used to re-read the macro closure's code every time (in a check load about 30% of the walk, 378 functions, 0.05-0.06 seconds; marshal and sha256 take
# 0.006 seconds). Digests are not remembered (they depend on live values — B1 of packages/doeff-hy/architecture.hy). An entry lists, per walk (a macro or the location of a value, and
# that module's file), the code reads the walk used. The entry key only decides how likely it is to hit; correctness is decided by the sha256 of the code
# inside.


#: Marker of the code-read entry key (raise the trailing number when the entry shape changes). A change to the way of reading itself (this module's code) is covered by the sha256 of this module's file
#: that goes into the key (:func:`_own_digest`), and a change to the Hy functions the read calls (``hy.mangle``, ``slashes2dots``) is covered by the Hy version
#: that goes into the key.
#: 2 = the shape with the field for whether it reads a location as a value added.
#: 3 = the shape that lists the reads with function type annotations and Hy temporary variable numbers removed, and the per-annotation reads (``!`` at the end of the row key).
FACTS_TAG = "doeff-hy/code-facts/3"

#: Values for the breadth of reads that cannot be followed by name (for checking rows read from storage).
_READS = frozenset(read.value for read in NamespaceRead)


class CodeReadings:
    """The code reads a walk uses — what was read from storage (code sha256 -> read), what this walk used, and whether anything was re-read because it was not in storage."""

    def __init__(self, stored: dict[str, CodeFacts], strict: bool = False) -> None:
        self._stored = stored
        #: Whether to use per-annotation reads (the walk of a closure that reads annotations as values — :func:`_code_facts`). For per-annotation reads the storage row key
        #: appends ``!`` to the code sha256 (the two reads of the same code are listed in one entry).
        self._strict = strict
        self.used: dict[str, CodeFacts] = {}
        self._mut_missed = False

    def facts(self, code: CodeType) -> CodeFacts:
        """The read of a function's code — in-process memory, storage memory (what a previous process read for code with the same contents), and re-reading, in that order."""
        sha = _code_sha256(code) + ("!" if self._strict else "")
        cache = _STRICT_FACTS if self._strict else _FACTS
        remembered = cache.get(id(code))
        if remembered is not None and remembered[0] is code:
            facts = remembered[1]
        elif sha in self._stored:
            facts = self._stored[sha]
            cache[id(code)] = (code, facts)
        else:
            facts = _code_facts(code, self._strict)
            self._mut_missed = True
        self.used[sha] = facts
        return facts

    @property
    def missed(self) -> bool:
        """Whether any code was re-read because it was not in storage (if so, write the entry after the walk)."""
        return self._mut_missed


#: In-process memory of the sha256 of code contents (code object id -> (code, sha256) — keeps the code alive to prevent reuse of ids).
_CODE_SHAS: dict[int, tuple[CodeType, str]] = {}


def _code_sha256(code: CodeType) -> str:
    """sha256 of the contents of a code object (marshal bytes — includes nested code, constants, name table, file name, and even line numbers. The same code with a different line number or file name
    gets a different key and is simply re-read)."""
    remembered = _CODE_SHAS.get(id(code))
    if remembered is not None and remembered[0] is code:
        return remembered[1]
    import marshal  # read only when the walk meets a function

    sha = hashlib.sha256(marshal.dumps(code)).hexdigest()
    _CODE_SHAS[id(code)] = (code, sha)
    return sha


def _facts_entry(key: "UsedMacroKey | ValueReference") -> str | None:
    """The path of the code-read entry for one walk (None if the setting does not use storage, or if this module's code cannot be read). Key = the marker, this module's
    code, the Python version (instruction numbers), the Hy version (an appendix for the mangle of hy.R module names), the location, and the file of the location's module
    (separated per working tree — the code sha256 includes the file name)."""
    import hy  # the walk is inside the verification of a Hy module — already loaded

    from doeff_hy_bytecode_guard import _code_store as code_store  # read only when storage is used

    store = code_store.store_dir()
    own = None if store is None else _own_digest()
    if store is None or own is None:
        return None
    match key:
        case UsedMacroKey(table=table, module=module, name=name):
            place = f"macro\0{table}\0{module}\0{name}"
        case ValueReference(module=module, name=name):
            place = f"value\0{module}\0{name}"
    parts = (FACTS_TAG, own, sys.version, hy.__version__, place, _module_file(module) or "")
    return code_store.entry_path(store, code_store.content_key(parts, b""), code_store.FACTS_SUFFIX)


@dataclass(frozen=True)
class OwnCode:
    """sha256 of the contents of this module's file (None if it cannot be read — the code of the way of reading is unknown, so storage is not used)."""

    sha256: str | None


#: Memory of this module's code (once per process — only one key, "own").
_OWN: dict[str, OwnCode] = {}


def _own_digest() -> str | None:
    """sha256 of the contents of this module's file — so a version with a changed way of reading does not pull the previous version's reads from storage."""
    known = _OWN.get("own")
    if known is None:
        try:
            with open(__file__, "rb") as stream:
                known = OwnCode(hashlib.sha256(stream.read()).hexdigest())
        except OSError:
            known = OwnCode(None)
        known = _OWN.setdefault("own", known)
    return known.sha256


#: Shape of the contents of a storage entry (the marshal boundary — field names and types are held by :class:`CodeFacts`; here only the order for writing and reading):
#: code sha256, digest shape, name table, string constants, imports (name, level), hy.R modules, breadth of reads that cannot be followed by name, location read.
FactsRow = tuple[
    str, str, tuple[str, ...], tuple[str, ...], tuple[tuple[str, int], ...], tuple[str, ...], str, bool
]


def _stored_facts(entry: str) -> dict[str, CodeFacts]:
    """Read the code-read entry in storage (empty if absent). An entry of the wrong shape is discarded by name, and empty is returned (the walk re-reads and adds again)."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # read only when storage is used

    data = code_store.read_entry(entry)
    if data is None:
        return {}
    import marshal  # read only when it hit

    try:
        rows = marshal.loads(data)
    except (EOFError, ValueError, TypeError) as error:
        code_store.discard_entry(entry, f"cannot read with marshal: {error}")
        return {}
    read = _facts_from_rows(rows)
    if read is None:
        code_store.discard_entry(entry, "code read has the wrong shape")
        return {}
    return read


def _write_facts(entry: str, used: dict[str, CodeFacts]) -> None:
    """Add the code reads the walk used to storage (when set not to write, only the named storage — the same rule as how loader_hooks adds code.
    Where storage cannot be written, silently do not add — storage is only for speed)."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # read only when storage is used

    if sys.dont_write_bytecode and not code_store.store_is_named():
        return
    import marshal  # read only when adding

    rows: tuple[FactsRow, ...] = tuple(
        (
            sha,
            facts.shape,
            facts.names,
            facts.texts,
            tuple((reference.name, reference.level) for reference in facts.imports),
            facts.called_modules,
            facts.reads.value,
            facts.reads_location,
        )
        for sha, facts in sorted(used.items())
    )
    code_store.write_entry(entry, marshal.dumps(rows))


def _facts_from_rows(rows: object) -> dict[str, CodeFacts] | None:
    """Assemble the sequence read from storage into a code read (None if the shape is wrong)."""
    if not isinstance(rows, tuple):
        return None
    read: dict[str, CodeFacts] = {}
    for row in rows:
        match row:
            case (
                str() as sha,
                str() as shape,
                tuple() as names,
                tuple() as texts,
                tuple() as imports,
                tuple() as called,
                str() as reads,
                bool() as location,
            ) if (
                _strings(names)
                and _strings(texts)
                and _strings(called)
                and all(_import_pair(pair) for pair in imports)
                and reads in _READS
            ):
                read[sha] = CodeFacts(
                    shape,
                    names,
                    texts,
                    tuple(ImportReference(name, level) for name, level in imports),
                    called,
                    NamespaceRead(reads),
                    location,
                )
            case _:
                return None
    return read


def _strings(values: tuple[object, ...]) -> bool:
    """Whether all the contents of the tuple are strings (decided by a set of types — one storage entry has thousands of names and strings)."""
    return set(map(type, values)) <= _STR_TYPE


def _import_pair(pair: object) -> bool:
    """Whether it has the shape of an import row (name, level)."""
    match pair:
        case (str(), int()):
            return True
        case _:
            return False


#: The set of types for a tuple of only strings (:func:`_strings`).
_STR_TYPE = frozenset({str})

#: The spelling of the byte length of one digest string (8-byte little endian), and how to build the bytes of a string (:meth:`_Walker.digest`).
_LENGTH = struct.Struct("<Q")
_ENCODE = operator.methodcaller("encode", "utf-8", "surrogatepass")

_IMPORT_NAME = dis.opmap["IMPORT_NAME"]
#: The right shift that gets the name-table index from the argument of IMPORT_NAME (from 3.15 the low 2 bits are the lazy and eager marker — PEP 810).
_IMPORT_NAME_SHIFT = 2 if sys.version_info >= (3, 15) else 0
_LOAD_CONST = dis.opmap["LOAD_CONST"]
_LOAD_SMALL_INT = dis.opmap.get("LOAD_SMALL_INT", -1)
_EXTENDED_ARG = dis.EXTENDED_ARG


@dataclass(frozen=True)
class _Unit:
    """One instruction (the instruction number, and the argument with EXTENDED_ARG folded in)."""

    op: int
    arg: int


def _units(code: CodeType) -> Iterator[_Unit]:
    """List the instructions of code in order (``co_code`` is the form before specialization, and the cache fields are 0 — not read as instructions). dis.get_instructions
    took 0.3 seconds once over all the functions of a macro's closure, so this is a light reader that reads only the two instructions needed (import and its relative level)."""
    raw = code.co_code
    extended = 0
    for offset in range(0, len(raw), 2):
        op, arg = raw[offset], raw[offset + 1]
        if op == _EXTENDED_ARG:
            extended = (extended | arg) << 8
            continue
        if op != 0:
            yield _Unit(op, extended | arg)
        extended = 0


def _imports(code: CodeType) -> Iterator[ImportReference]:
    """The import instructions inside a function (to put into the closure the helpers that a macro imports only at expansion time). The relative level is the instruction two
    before the import instruction (pushed in the order level number, fromlist, import). Code that has no import instruction (most functions of a closure) just has the instruction-number
    sequence (the bytes at even positions) searched in C and no instructions read (reading all the instructions here used to be about 1/4 of the macro closure walk)."""
    if _IMPORT_NAME not in code.co_code[::2]:
        return
    previous: tuple[_Unit | None, _Unit | None] = (None, None)
    for unit in _units(code):
        if unit.op == _IMPORT_NAME:
            match previous[0]:
                case _Unit(op=op, arg=arg) if op == _LOAD_SMALL_INT:
                    level = arg
                case _Unit(op=op, arg=arg) if op == _LOAD_CONST and isinstance(
                    code.co_consts[arg], int
                ):
                    level = int(code.co_consts[arg])
                case _:
                    level = 0
            yield ImportReference(code.co_names[unit.arg >> _IMPORT_NAME_SHIFT], level)
        previous = (previous[1], unit)


def _opcode(name: str) -> int:
    """The instruction number (-1 for an instruction that does not exist in this Python version)."""
    return dis.opmap.get(name, -1)


#: Instructions that read a name-table name as a global, and the right shift that gets the name-table index from the argument (from 3.11 LOAD_GLOBAL is one, from 3.12 LOAD_ATTR is
#: one, and LOAD_SUPER_ATTR is two).
_GLOBAL_NAME_LOADS: dict[int, int] = {
    _opcode("LOAD_GLOBAL"): 1 if sys.version_info >= (3, 11) else 0,
    _opcode("LOAD_NAME"): 0,
    _opcode("LOAD_FROM_DICT_OR_GLOBALS"): 0,
    _opcode("IMPORT_FROM"): 0,
}
_ATTRIBUTE_NAME_LOADS: dict[int, int] = {
    _opcode("LOAD_ATTR"): 1 if sys.version_info >= (3, 12) else 0,
    _opcode("LOAD_METHOD"): 0,
    _opcode("LOAD_SUPER_ATTR"): 2,
}
#: Instructions that use a name-table name without reading it (write, delete, the module name of an import), and the right shift that gets the name-table index from the argument — if the marker name of a read that cannot be followed by name
#: appears only in these, it is not a read.
_OTHER_NAME_USES: dict[int, int] = {
    op: shift
    for op, shift in {
        _opcode("STORE_ATTR"): 0,
        _opcode("DELETE_ATTR"): 0,
        _opcode("STORE_GLOBAL"): 0,
        _opcode("DELETE_GLOBAL"): 0,
        _opcode("STORE_NAME"): 0,
        _opcode("DELETE_NAME"): 0,
        _IMPORT_NAME: _IMPORT_NAME_SHIFT,
    }.items()
    if op != -1
}


def _namespace_read(code: CodeType) -> NamespaceRead:
    """The breadth of reads that cannot be followed by name in one code object (excluding nested code) — the marker names are split by whether they are read as a global or as an attribute. If a marker name
    appears in none of the instructions that can be split (use by an unknown instruction), it is judged MODULE on the safe side."""
    marks = _UNTRACEABLE_NAMES.intersection(code.co_names)
    if not marks:
        return NamespaceRead.NONE
    uses = tuple(_name_uses(code))
    read = _widest(tuple(_mark_read(use) for use in uses if use.name in marks))
    unexplained = marks - {use.name for use in uses}
    return NamespaceRead.MODULE if unexplained else read


class NameWay(enum.Enum):
    """How a name-table name is used (a global read, an attribute read, or other)."""

    GLOBAL = "global"
    ATTRIBUTE = "attribute"
    OTHER = "other"


@dataclass(frozen=True)
class NameUse:
    """One use of a name-table name in code (the name, how it is used, and — for a global name of :data:`_OBJECT_READ_CALLS` — whether it is called with one plain
    argument)."""

    name: str
    way: NameWay
    one_argument: bool = False


def _name_uses(code: CodeType) -> Iterator[NameUse]:
    """The instructions of code that use a name-table name."""
    units = tuple(_units(code))
    for index, unit in enumerate(units):
        if unit.op in _GLOBAL_NAME_LOADS:
            name = code.co_names[unit.arg >> _GLOBAL_NAME_LOADS[unit.op]]
            yield NameUse(
                name,
                NameWay.GLOBAL,
                name in _OBJECT_READ_CALLS and _one_plain_argument(units, index),
            )
        elif unit.op in _ATTRIBUTE_NAME_LOADS:
            yield NameUse(
                code.co_names[unit.arg >> _ATTRIBUTE_NAME_LOADS[unit.op]], NameWay.ATTRIBUTE
            )
        elif unit.op in _OTHER_NAME_USES:
            yield NameUse(code.co_names[unit.arg >> _OTHER_NAME_USES[unit.op]], NameWay.OTHER)


#: Instructions that push one value without popping (a local, a cell, a constant, a global or module-level name) — the start of a plain argument.
_VALUE_LOADS = frozenset(
    _opcode(name)
    for name in (
        "LOAD_FAST",
        "LOAD_FAST_BORROW",
        "LOAD_FAST_CHECK",
        "LOAD_DEREF",
        "LOAD_CONST",
        "LOAD_SMALL_INT",
        "LOAD_NAME",
        "LOAD_GLOBAL",
    )
) - {-1}
_LOAD_GLOBAL = _opcode("LOAD_GLOBAL")
_LOAD_ATTR = _opcode("LOAD_ATTR")
_PUSH_NULL = _opcode("PUSH_NULL")
_PRECALL = _opcode("PRECALL")
_CALL = _opcode("CALL")


def _one_plain_argument(units: tuple[_Unit, ...], index: int) -> bool:
    """Whether the global name loaded at ``units[index]`` is the callee of a call with exactly one positional argument that is a plain value read — one load of a local,
    a cell, a constant or a global, followed by attribute reads (``vars(cls)``, ``vars(self.owner)``). The callee is pinned by the NULL pushed with it (the low bit of
    LOAD_GLOBAL from 3.11, or a PUSH_NULL beside LOAD_NAME), and the call is ``CALL 1`` (``PRECALL 1`` up to 3.11). Any other shape (no argument, a computed argument,
    ``*``, a keyword, the name passed as a value) is false — read on the safe side as MODULE."""
    load = units[index]
    pushes_null = (load.op == _LOAD_GLOBAL and load.arg & 1 == 1) or any(
        0 <= at < len(units) and units[at].op == _PUSH_NULL for at in (index - 1, index + 1)
    )
    if not pushes_null:
        return False
    rest = units[index + 1 :]
    if rest and rest[0].op == _PUSH_NULL:
        rest = rest[1:]
    if not rest or rest[0].op not in _VALUE_LOADS:
        return False
    if rest[0].op == _LOAD_GLOBAL and rest[0].arg & 1 == 1:
        return False
    position = 1
    while position < len(rest) and rest[position].op == _LOAD_ATTR:
        if sys.version_info >= (3, 12) and rest[position].arg & 1 == 1:
            return False  # a method load (the attribute is called)
        position += 1
    return (
        position < len(rest)
        and rest[position].op in (_CALL, _PRECALL)
        and rest[position].arg == 1
    )


def _mark_read(use: NameUse) -> NamespaceRead:
    """The breadth of the read for one use of a marker name."""
    match use:
        case NameUse(way=NameWay.GLOBAL, name=name, one_argument=True) if name in _OBJECT_READ_CALLS:
            return NamespaceRead.OBJECT
        case NameUse(way=NameWay.GLOBAL, name=name) if name in _MODULE_READ_GLOBALS:
            return NamespaceRead.MODULE
        case NameUse(way=NameWay.GLOBAL, name=name) if name in _OBJECT_READ_GLOBALS:
            return NamespaceRead.OBJECT
        case NameUse(way=NameWay.ATTRIBUTE, name=name) if name in _MODULE_READ_ATTRIBUTES:
            return NamespaceRead.MODULE
        case NameUse(way=NameWay.ATTRIBUTE, name=name) if name in _OBJECT_READ_ATTRIBUTES:
            return NamespaceRead.OBJECT
        case NameUse():
            return NamespaceRead.NONE


def _widest(reads: tuple[NamespaceRead, ...]) -> NamespaceRead:
    """The widest of the read breadths."""
    if NamespaceRead.MODULE in reads:
        return NamespaceRead.MODULE
    if NamespaceRead.OBJECT in reads:
        return NamespaceRead.OBJECT
    return NamespaceRead.NONE


def _called_modules(texts: tuple[str, ...]) -> Iterator[str]:
    """The module of a ``hy.R.<module>.<name>`` call a macro builds — a quoted symbol becomes the expression ``(. hy R <module> <name>)``, and in the code's
    string constants <module> appears first right after "R" (constants are ordered by first use). The form of a single symbol string
    (``"hy.R.<module>.<name>"``) is also read. A form that cannot be read (no constant after R) is an empty name (the walk side covers it with the function's module file)."""
    for index, text in enumerate(texts):
        if text == "R" and "hy" in texts:
            yield _reference_module(texts[index + 1]) if index + 1 < len(texts) else ""
        elif text.startswith("hy.R."):
            for match in _HY_R.finditer(text):
                yield _reference_module(match.group(1))


_COMPILED_CALLABLES = (
    types.BuiltinFunctionType,
    types.MethodDescriptorType,
    types.WrapperDescriptorType,
    types.ClassMethodDescriptorType,
    types.MethodWrapperType,
    types.GetSetDescriptorType,
    types.MemberDescriptorType,
)

_PLAIN_VALUES = (type(None), bool, int, float, complex, str, bytes, type(Ellipsis))


def _plain(value: object) -> bool:
    """Whether it enters by value — plain values, tuples and frozensets whose contents are plain values (nested too), and sets whose contents are only those."""
    match value:
        case set():
            return all(_frozen_plain(item) for item in value)
        case _:
            return _frozen_plain(value)


def _frozen_plain(value: object) -> bool:
    """Whether it is a plain value that cannot be rewritten (a plain value, or a tuple or frozenset whose contents are only those)."""
    match value:
        case tuple() | frozenset():
            return all(_frozen_plain(item) for item in value)
        case _:
            return isinstance(value, _PLAIN_VALUES)


def _plain_text(value: object) -> str:
    """Text of a container of plain values that does not depend on order (a set is sorted by element text — the hash of str differs per process)."""
    match value:
        case tuple():
            return "(" + ",".join(_plain_text(item) for item in value) + ")"
        case frozenset() | set():
            return "{" + ",".join(sorted(_plain_text(item) for item in value)) + "}"
        case _:
            return f"{type(value).__name__}:{value!r}"


class _Walker:
    """A walk that builds one digest of a code closure. The same function or class is entered by number from its second encounter (cycles and sharing)."""

    def __init__(self, sha256_of: Sha256Of, readings: CodeReadings) -> None:
        #: Strings that go into the digest (spelled in order by :meth:`walked`).
        self._parts: list[str] = []
        self._sha256_of = sha256_of
        #: Reads of function code (uses reads a previous process remembered in storage, and collects the reads this walk used).
        self.readings = readings
        #: Object id of what was followed -> (order number, object — keeps objects alive during the walk to prevent reuse of ids).
        self._seen: dict[int, tuple[int, object]] = {}
        #: Referenced modules (name -> module), and the attribute names entered.
        self._modules: dict[str, ModuleType] = {}
        self._attributes: dict[str, set[str]] = {}
        #: String constants of the code of the followed functions (candidate names for ``getattr(m, "name")`` and ``d["name"]``).
        self._texts: set[str] = set()
        #: Owner module name of what was followed -> file (decides whether a digest memory is valid; the record rows of modules covered by file).
        self.files: dict[str, str] = {}
        #: Names of modules that could not be imported (only the name goes into the digest, and it is covered by the record's file rows).
        self.unimportable: set[str] = set()
        #: Whether it met a module in the middle of an import or a module that cannot be imported, or whether a file read from the current disk differs from when the module was executed
        #: (that digest depends on import progress or on stale in-process values, so it is neither remembered nor put in the record).
        self._mut_partial = False
        #: Whether it hit an environment variable read (that digest does not capture the environment variable values and so cannot be verified — it is not remembered, and the record row gets the marker ENVIRONMENT_READ).
        self._mut_environment = False
        #: Globals covered by source statements (module names, global names — the second time only the marker goes in).
        self._bound_names: set[tuple[str, str]] = set()
        #: Where the code contents of a function first encountered live (the number in :attr:`_parts` -> function number, function, whether it reads function contents as a value — in number order).
        self._slots: dict[int, _SlotPlace] = {}
        #: Numbers of the functions and classes currently being walked (the order number in :attr:`_seen` — the last is the one being walked now).
        self._mut_stack: tuple[int, ...] = ()
        #: Function or class number -> the numbers of the functions and classes encountered while walking its contents (both first and second encounters).
        self._edges: dict[int, set[int]] = {}
        #: Numbers of functions and classes encountered beyond a binding statement (inside :meth:`_bound`) — the function contents of those and what is reachable from them are not narrowed.
        self._state_roots: set[int] = set()
        #: Nesting depth of :meth:`_bound` (0 = outside a binding statement).
        self._mut_state_depth = 0

    def walked(self) -> "ClosureWalk":
        """The material of what was followed — after finishing adding the string-constant-named attributes of the referenced modules. The accumulated strings are spelled with the byte length of each
        (8-byte little endian) in front (calling hash per string came to 28,000 calls in a check load),
        and split at the function code contents (whether to cover the contents is decided by the record and verification — :meth:`ClosureWalk.digest`)."""
        self._attributes_named_by_texts()
        encoded = list(map(_ENCODE, self._parts))
        framed = list(
            chain.from_iterable(zip(map(_LENGTH.pack, map(len, encoded)), encoded, strict=True))
        )
        cuts = (-1, *self._slots, len(encoded))
        segments = tuple(
            b"".join(framed[2 * (start + 1) : 2 * end]) for start, end in pairwise(cuts)
        )
        self._parts.clear()
        places = tuple(self._slots.values())
        forced_nodes = self._reached_from(self._state_roots)
        return ClosureWalk(
            segments,
            tuple(place.slot for place in places),
            _bits(index for index, place in enumerate(places) if place.node in forced_nodes),
            _bits(index for index, place in enumerate(places) if place.reads_code),
        )

    def _reached_from(self, roots: set[int]) -> frozenset[int]:
        """From roots, follow what was encountered while walking contents and return the numbers of the reachable functions and classes (roots included)."""
        reached: frozenset[int] = frozenset()
        frontier = frozenset(roots)
        while frontier:
            reached |= frontier
            frontier = (
                frozenset(chain.from_iterable(self._edges.get(node, ()) for node in frontier))
                - reached
            )
        return reached

    def _attributes_named_by_texts(self) -> None:
        """Beyond reads that do not look up by name (``getattr(m, "name")``, string keys) — take the string constants encountered in the walk as names, and enter the
        same-named attribute of the referenced modules (repeat while the entered attributes bring in new modules and strings). Covered even when the reading function and the module reference
        are in different functions (the module is passed as an argument)."""
        while True:
            # Pick first, by set intersection, only the strings that are in the namespace (_text_attribute treats a name not in the namespace as false — judging module x string
            # one by one exceeded 30,000 times in a check load). The order is the same as before (module name, string order).
            pending = tuple(
                (name, text)
                for name in sorted(self._modules)
                for text in sorted(
                    self._texts.intersection(vars(self._modules[name])) - self._attributes[name]
                )
                if _text_attribute(self._modules[name], text)
            )
            if not pending:
                return
            for name, text in pending:
                if text not in self._attributes[name]:
                    self._attribute(self._modules[name], text, ())

    def _feed(self, *parts: str) -> None:
        """Add a string to the digest (:meth:`walked` spells it with the length in front — two sequences with different breaks do not produce the same digest)."""
        self._parts.extend(parts)

    def _first_visit(self, value: object) -> bool:
        """Whether this is the first time following the object (from the second time only the number goes in — it does not stop on cycles, and separates sharing from duplication). In either case it remembers
        the encounter from the function or class currently being walked, and the encounter beyond a binding statement (decides the functions not narrowed — :meth:`walked`)."""
        seen = self._seen.get(id(value))
        node = len(self._seen) if seen is None else seen[0]
        if self._mut_stack:
            self._edges.setdefault(self._mut_stack[-1], set()).add(node)
        if self._mut_state_depth:
            self._state_roots.add(node)
        if seen is not None:
            self._feed("seen", str(seen[0]))
            return False
        self._seen[id(value)] = (node, value)
        return True

    def _node(self, value: object) -> int:
        """The number of a followed object (looked up after :meth:`_first_visit`)."""
        return self._seen[id(value)][0]

    def _note(self, module: ModuleType | None) -> None:
        """Remember the file of the owner module of what was followed (used to judge whether a digest memory is valid and for file-level record rows)."""
        file = None if module is None else vars(module).get("__file__")
        if module is not None and isinstance(file, str):
            self.files[module.__name__] = file

    def _against_executed(self, file: str) -> None:
        """Check whether the parts read from the current disk (the file sha256, the statements that cover a value) are from the same point in time as the module's values in the process — if the file was
        rewritten after the module was executed (a resident process), this digest does not reflect the values the expansion used, so as with the in-the-middle-of-import state
        it is neither remembered nor put in the record (the record row is None — a new process judges it stale and expands again)."""
        if not loader_hooks.executed_source_is_current(file):
            self._mut_partial = True

    def _note_environment(self, reads: bool) -> None:
        """Remember that an environment variable read was hit — to make this digest an unverifiable marker (:func:`_remembered`)."""
        if reads:
            self._mut_environment = True

    def _note_location(self, reads: bool) -> None:
        """Remember that code or a binding statement that reads a module's location as a value was hit — the walk does not cover the contents of the files followed from the location (neighboring
        data, stubs), so as with environment variable reads this makes it an unverifiable marker (:data:`_LOCATION_NAMES`)."""
        self._note_environment(reads)

    def _fold(self, module: ModuleType | None) -> None:
        """Safe side: add the sha256 of the module's file to the digest (if it cannot be read, a marker that differs every time — always judged stale)."""
        file = None if module is None else vars(module).get("__file__")
        if module is None or not isinstance(file, str):
            self._feed("file", "none" if module is None else module.__name__)
            return
        self.files[module.__name__] = file
        self._against_executed(file)
        digest = self._sha256_of(file)
        self._feed("file", module.__name__, digest if digest is not None else os.urandom(16).hex())

    def _fold_opaque(self, module: ModuleType | None) -> None:
        """Cover a value that cannot be walked (a value not looked up by a global name, a global whose binding cannot be followed, the target of a read that cannot be followed by name) by the owner module's file
        (:meth:`_fold`). The file does not reflect environment variables the owner's source read at load time, so if the source may read environment variables,
        make it an unverifiable marker (the form that holds a table built from environment variables in a default or closure cell)."""
        self._fold(module)
        file = None if module is None else vars(module).get("__file__")
        names = bound_source.source_names(file) if isinstance(file, str) else None
        self._note_environment(names is not None and _source_reads_environment(names))

    def _lazy_module(
        self, module_name: str, home: ModuleType | None, names: tuple[str, ...]
    ) -> ModuleType | None:
        """Import targets inside functions, modules under a package, and hy.R targets — import them and enter the attributes named in the name table. When import fails
        (e.g. requiring a module in the middle of a circular import), enter only the name and treat it as a digest of the in-the-middle-of-import state
        (not remembered, and the record row is compared by file). Names that cannot be read are covered by the function's module file."""
        if not module_name:
            self._fold(home)
            return None
        if _named_only(module_name):
            self._feed("module", module_name)
            # Read environment variables with an os imported inside a function (even if there is no os among the globals).
            self._note_environment(
                module_name in _ENVIRONMENT_MODULES and not _ENVIRONMENT_NAMES.isdisjoint(names)
            )
            return None
        match _import_for_digest(module_name):
            case ModuleType() as module:
                self._module(module, names)
                return module
            case NotImportable():
                self._feed("unimportable", module_name)
                self.unimportable.add(module_name)
                self._mut_partial = True
                return None

    def function(self, function: FunctionType) -> None:
        """Follow a function's code, the globals looked up by name, defaults, and closure cells."""
        module_name = function.__module__ if isinstance(function.__module__, str) else ""
        if _named_only(module_name):
            self._feed("function-name", module_name, function.__qualname__)
            self._note_environment(_environment_object(function))  # getenv imported from os, etc.
            return
        if not self._first_visit(function):
            return
        outer = self._mut_stack
        self._mut_stack = (*outer, self._node(function))
        try:
            self._function_body(function, module_name)
        finally:
            self._mut_stack = outer

    def _function_body(self, function: FunctionType, module_name: str) -> None:
        """The contents of a function met for the first time (the location of the code contents is remembered, and the record and verification decide whether to cover them — :class:`ClosureWalk`)."""
        home = sys.modules.get(module_name)
        self._note(home)
        facts = self.readings.facts(function.__code__)
        self._texts.update(facts.texts)
        self._feed("function", module_name, function.__qualname__)
        self._slots[len(self._parts)] = _SlotPlace(
            self._node(function),
            CodeSlot(function.__code__, facts.shape),
            _reads_code(facts) or _reads_code_by_alias(function.__globals__, facts.names),
        )
        self._feed(facts.shape)
        # At expansion time or load time, the code reads environment variables or reads a module's location as a value.
        self._note_environment(_reads_environment(function.__globals__, facts.names))
        self._note_location(facts.reads_location)
        self._value(function.__defaults__, home, facts.names)
        # The default of a keyword-only argument is a dict of name -> value (entered in name order — entering it as a dict would make it an unwalkable value).
        for keyword, default in sorted((function.__kwdefaults__ or {}).items()):
            self._feed("kwdefault", keyword)
            self._value(default, home, facts.names)
        for cell in function.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:  # a cell before binding
                self._feed("empty-cell")
                continue
            self._value(content, home, facts.names)
        namespace = function.__globals__
        # Location of a global name (only when the function's globals are the module's namespace itself — covers an unwalkable value by the source statement of that name).
        owner = home if home is not None and vars(home) is namespace else None
        for name in facts.names:
            if name in namespace:
                self._feed("global", name)
                if name in _LOCATION_NAMES:
                    self._feed("location")
                    continue
                self._value(
                    namespace[name],
                    home,
                    facts.names,
                    None if owner is None else GlobalName(owner, name),
                )
        package = namespace.get("__package__")
        for reference in facts.imports:
            self._lazy_module(
                _import_target(reference, package if isinstance(package, str) else None),
                home,
                facts.names,
            )
        for called in facts.called_modules:
            self._called_macros(called, facts.texts, home)
        match facts.reads:
            case NamespaceRead.MODULE:
                self._fold_opaque(home)
                self._fold_referenced_modules(namespace, facts.names)
            case NamespaceRead.OBJECT:
                self._fold_referenced_modules(namespace, facts.names)
            case NamespaceRead.NONE:
                pass

    def _fold_referenced_modules(
        self, namespace: dict[str, object], names: tuple[str, ...]
    ) -> None:
        """Add the sha256 of the files of the modules the function references by global name (modules that can be the target of a read that cannot be followed by name)."""
        for name in names:
            value = namespace.get(name)
            if isinstance(value, ModuleType) and not _named_only(value.__name__):
                self._fold_opaque(value)

    def outside_value(self, module: ModuleType, value: object) -> None:
        """Enter a value (a module attribute) outside the macro that changes the expansion result."""
        self._feed("outside", module.__name__)
        self._note(module)
        self._value(value, module, ())

    def _called_macros(
        self, module_name: str, texts: tuple[str, ...], home: ModuleType | None
    ) -> None:
        """Targets of the hy.R calls a macro builds — follow all the module's macros whose names appear in the code's string constants (the name cannot
        be pinned to one from the constant sequence — if it overlaps a constant used earlier, it does not appear in the sequence)."""
        module = self._lazy_module(module_name, home, ())
        table = None if module is None else vars(module).get(MACRO_TABLE)
        if not isinstance(table, dict):
            return
        for text in texts:
            macro = dict.get(table, _mangle(text)) if text else None
            if isinstance(macro, FunctionType):
                self._feed("hy.R", module_name, text)
                self.function(macro)

    def module_content(self, module: ModuleType) -> None:
        """Functions, classes, and plain values defined in the module (in name order)."""
        self._feed("module-content", module.__name__)
        self._note(module)
        if _initializing(module):
            self._feed("initializing")
            self._fold(module)
            self._mut_partial = True
            return
        namespace = vars(module)
        for name in sorted(namespace):
            value = namespace[name]
            defined_here = (
                isinstance(value, (FunctionType, type)) and value.__module__ == module.__name__
            )
            if defined_here or isinstance(value, _PLAIN_VALUES):
                self._feed("member", name)
                self._value(value, module, ())

    def _value(
        self,
        value: object,
        home: ModuleType | None,
        names: tuple[str, ...],
        bound: "GlobalName | None" = None,
    ) -> None:
        """Enter one value (the rules per type are in the module docstring). bound = where the value lives when it was looked up by a module global name (covers an unwalkable value
        by the source statement of that name)."""
        match value:
            case None | bool() | int() | float() | complex() | str() | bytes():
                kind = type(value)
                self._feed("value", f"{kind.__module__}.{kind.__qualname__}", repr(value))
            case tuple():
                # Tuples are not folded by object identity — whether tuples with the same contents are shared differs per process (sharing of constant tuples).
                self._feed("tuple", str(len(value)))
                for item in value:
                    self._value(item, home, names)
            case frozenset() | set() if _plain(value):
                # A set whose contents are only plain values enters by value (the constant table of Hy's #{...} becomes a set).
                self._feed(type(value).__name__, _plain_text(value))
            case FunctionType():
                self.function(value)
            case type():
                self._class(value)
            case ModuleType():
                self._module(value, names)
            case _:
                self._wrapped_value(value, home, names, bound)

    def _wrapped_value(
        self,
        value: object,
        home: ModuleType | None,
        names: tuple[str, ...],
        bound: "GlobalName | None",
    ) -> None:
        """Enter values wrapping a function (staticmethod, property, partial, bound method), enums, C functions, Hy values, and unwalkable values."""
        match value:
            case staticmethod() | classmethod():
                self._feed(type(value).__name__)
                self._value(value.__func__, home, names)
            case property():
                self._feed("property")
                for accessor in (value.fget, value.fset, value.fdel):
                    self._value(accessor, home, names)
            case functools.partial():
                self._feed("partial")
                self._value(value.func, home, names)
                self._value(value.args, home, names)
                self._value(value.keywords, home, names)
            case types.MethodType():
                self._feed("method")
                self._value(value.__func__, home, names)
                self._value(value.__self__, home, names)
            case enum.Enum():
                self._feed("enum", value.name)
                self._value(value.value, home, names)
                self._class(type(value))
            case _ if type(value).__qualname__ == "_tuplegetter" and type(value).__module__ in (
                "collections",
                "_collections",
            ):
                # The read of a namedtuple field (``Alias for field number N`` — only the field number is behavior).
                self._feed("tuplegetter", str(value.__doc__))
            case types.MemberDescriptorType() if _python_source(
                sys.modules.get(value.__objclass__.__module__)
            ):
                self._slot(value)
            case _ if isinstance(value, _COMPILED_CALLABLES):
                self._compiled(value, home)
            case _ if type(value).__module__.partition(".")[0] == "hy":
                kind = type(value)
                self._feed("hy-value", kind.__qualname__, repr(value))
            case _:
                self._state_value(value, home, names, bound)

    def _state_value(
        self,
        value: object,
        home: ModuleType | None,
        names: tuple[str, ...],
        bound: "GlobalName | None",
    ) -> None:
        """Among values that do not wrap a function, those whose contents cannot be entered by value (ContextVar, a frozen dataclass value, tables, unwalkable objects)."""
        match value:
            case contextvars.ContextVar():
                # Cover by name and default. The current value is not entered — it is execution state, not a table that decides the expansion result (an expansion while the type-check expansion marker
                # TYPE_CHECK_EXPANSION is true does not attach a record — loader_hooks).
                self._feed("context-variable", value.name)
                self._context_default(value, home, names)
            case _ if _environment_object(value):
                # The environment variable table itself (an environ imported from os under an alias, a default, a closure cell) — its contents are process state, and neither the binding
                # statement nor the file covers it.
                self._feed("environment")
                self._note_environment(True)
            case _ if _frozen_dataclass_instance(value):
                # A frozen dataclass value (binding_forms' _TOP, the default of ModuleNames(), etc.) — enter it as the class and the field values.
                self._feed("frozen-instance")
                self._class(type(value))
                for field in dataclasses.fields(value):
                    self._feed("field", field.name)
                    self._value(getattr(value, field.name), home, names)
            case _ if bound is not None:
                # An unwalkable value was looked up by a module global name — cover it by the source statement that binds and rewrites that name. Lists and dicts go here too.
                # The contents of in-process memory (doeff_hy.macros's _RESUME_ANALYSIS_CACHE is a dict keyed by a tuple of object ids, and a list
                # accumulating the order of calls) change during execution, and entering the contents would make the digest of the same macro differ per process and be judged
                # stale at every verification. The initial contents and the rewrites are written in the source.
                self._feed("opaque", type(value).__module__, type(value).__qualname__)
                self._bound(bound)
            case _:
                # An unwalkable value was looked up not by a global name (a default, a closure cell, inside a tuple, a class value) — cover it by the owner module's file
                # (an unverifiable marker if the source may read environment variables).
                self._feed("opaque", type(value).__module__, type(value).__qualname__)
                self._fold_opaque(home)

    def _context_default(
        self,
        variable: "contextvars.ContextVar[object]",
        home: ModuleType | None,
        names: tuple[str, ...],
    ) -> None:
        """The default of a ContextVar (read inside an empty Context — if there is no default, enter that fact). A plain value enters by value, otherwise by the type's name."""
        try:
            default = contextvars.Context().run(variable.get)
        except LookupError:
            self._feed("no-default")
            return
        if _plain(default):
            self._value(default, home, names)
        else:
            kind = type(default)
            self._feed("default-type", kind.__module__, kind.__qualname__)

    def _bound(self, bound: "GlobalName") -> None:
        """Cover one module global by the source statement that binds and rewrites that name (:mod:`doeff_hy_bytecode_guard._bound_source`).
        The names the statement reads are followed by the same rules, top-level definitions that may rewrite the name in their body have that function or class followed, and names bound by import are
        followed in the module of the import target. When the source cannot be read, no binding is found, or there is a binding that cannot be followed, cover it by the module's file.
        The functions and classes met here (and what is reachable from them) are the code that made the value — it ran outside the expansion (at load time or in an earlier expansion) and made the value,
        so the contents of those functions' code are not narrowed (:meth:`walked`)."""
        module, name = bound.module, bound.name
        self._feed("bound", module.__name__, name)
        if (module.__name__, name) in self._bound_names:
            return
        self._bound_names.add((module.__name__, name))
        self._mut_state_depth += 1
        try:
            self._bound_statements(module, name)
        finally:
            self._mut_state_depth -= 1

    def _bound_statements(self, module: ModuleType, name: str) -> None:
        """Follow the binding statement, the rewriting functions, and the import targets of one global (the body of :meth:`_bound`)."""
        self._note(module)
        namespace = vars(module)
        file = namespace.get("__file__")
        if isinstance(file, str):
            self._against_executed(file)
        binding = bound_source.binding_of(file, name) if isinstance(file, str) else None
        if binding is None or binding.unresolved or not (binding.bound or binding.imports):
            self._fold_opaque(module)
            return
        for statement in binding.statements:
            self._feed("statement", statement.text)
            # At load time the statement reads environment variables to build the value (a Hy statement holds each part of a symbol in loads) — the statement's text does not hold the environment variable values
            # (the shape of appendix 3).
            self._note_environment(
                _reads_environment(namespace, (*statement.loads, *statement.attributes))
            )
            # At load time the statement reads a neighboring file from the location to build the value (``TABLE = json.loads(Path(__file__).with_name(...)...)``).
            self._note_location(
                not _LOCATION_NAMES.isdisjoint((*statement.loads, *statement.attributes))
            )
            for load in statement.loads:
                if load != name and load in namespace:
                    self._feed("load", load)
                    self._value(
                        namespace[load], module, statement.attributes, GlobalName(module, load)
                    )
        for mutator in binding.mutators:
            value = namespace.get(mutator)
            self._feed("mutator", mutator)
            if isinstance(value, (FunctionType, type)):
                self._value(value, module, ())
            else:
                self._fold_opaque(module)
        for imported in binding.imports:
            self._imported_binding(module, imported)

    def _imported_binding(self, module: ModuleType, imported: bound_source.ImportedName) -> None:
        """Follow a global bound by import through the same name in the import target's module (importing a module itself counts as a module reference)."""
        package = vars(module).get("__package__")
        target = _import_target(
            ImportReference(imported.module, imported.level),
            package if isinstance(package, str) else None,
        )
        self._feed("imported", target, imported.name)
        source = None if not target or _named_only(target) else sys.modules.get(target)
        if not target:
            self._fold_opaque(module)
        elif source is None:
            # The standard library or Hy itself (covered by the version), or a module that is not loaded (the import name does not match the value).
            if not _named_only(target):
                self._fold_opaque(module)
        elif not imported.name:
            self._module(source, ())
        elif imported.name in vars(source):
            self._value(vars(source)[imported.name], source, (), GlobalName(source, imported.name))
        else:
            self._fold_opaque(module)

    def _class(self, cls: type) -> None:
        """Follow a class's bases and contents (methods, class values)."""
        module_name = cls.__module__ if isinstance(cls.__module__, str) else ""
        if _named_only(module_name):
            self._feed("class-name", module_name, cls.__qualname__)
            return
        if not self._first_visit(cls):
            return
        outer = self._mut_stack
        self._mut_stack = (*outer, self._node(cls))
        try:
            self._class_body(cls, module_name)
        finally:
            self._mut_stack = outer

    def _class_body(self, cls: type, module_name: str) -> None:
        """The bases and contents of a class met for the first time."""
        home = sys.modules.get(module_name)
        self._note(home)
        self._feed("class", module_name, cls.__qualname__)
        if not _python_source(home):
            self._extension_class(cls, home)
            return
        for base in cls.__bases__:
            self._value(base, home, ())
        members = vars(cls)
        for name in sorted(members):
            if name in _CLASS_SKIP:
                continue
            self._feed("member", name)
            # In 3.15 a dataclass's __doc__ is a descriptor that builds the text from the signature on first read (dataclasses._AutoDocstring) — enter the text read
            # from the class (the same value as up to 3.14; left as a descriptor it would be an unwalkable value covered by the module's file).
            value = cls.__doc__ if name == "__doc__" else members[name]
            self._value(value, home, ())

    def _extension_class(self, cls: type, home: ModuleType | None) -> None:
        """Cover a class of an extension module (a class with no Python source) not by the whole .so file but by its name and public shape — the bases (followed by the same
        rules), and the sequence of attribute names and kinds (method, descriptor, value — a plain value by its value, otherwise by the type's name)."""
        self._feed("extension-class")
        for base in cls.__bases__:
            self._value(base, home, ())
        members = vars(cls)
        for name in sorted(members):
            if name in _CLASS_SKIP:
                continue
            self._feed("member", name, *_public_kind(members[name]))

    def _module(self, module: ModuleType, names: tuple[str, ...]) -> None:
        """A module reference — the name, and the attributes that appear in the code's name table (reads of ``module.name``). A module under a package is covered by file
        (whether it appears as an attribute changes depending on whether it was loaded). Attributes named by string constants are entered at the end of the walk."""
        self._feed("module", module.__name__)
        if _named_only(module.__name__):
            return
        if _initializing(module):
            # The namespace of a module in the middle of an import (the module being expanded itself, or a module importing it) is not yet complete —
            # reading attributes would make the digest of the same macro change with import progress. Cover it by the sha256 of the file, and do not remember this digest.
            self._feed("initializing")
            self._fold(module)
            self._mut_partial = True
            return
        if module.__name__ not in self._modules:
            self._modules[module.__name__] = module
            self._attributes[module.__name__] = set()
            self._note(module)
        for name in names:
            if name in self._attributes[module.__name__]:
                continue
            submodule = _submodule(module, name)
            if submodule is not None:
                self._attributes[module.__name__].add(name)
                self._feed("attribute", module.__name__, name)
                self._lazy_module(submodule, module, names)
            elif name in vars(module):
                self._attribute(module, name, names)

    def _attribute(self, module: ModuleType, name: str, names: tuple[str, ...]) -> None:
        """Enter one module attribute (once per module — an attribute that merely names a location gets only a marker, not the value; :data:`_LOCATION_NAMES`)."""
        self._attributes[module.__name__].add(name)
        self._feed("attribute", module.__name__, name)
        if name in _LOCATION_NAMES:
            self._feed("location")
            return
        self._value(vars(module)[name], module, names, GlobalName(module, name))

    def _slot(self, value: types.MemberDescriptorType) -> None:
        """A slot field of a class with Python source (``__slots__`` — the fields of a defrecord type) — the descriptor is made by the interpreter from the field name, so it is
        entered by the owner type and the field name, and the type is followed by its contents (its ``__slots__`` row gives the order of the fields). Covering it as a C
        function by the owner module's file made a function added anywhere in _clause_endings.hy make every defhandler user stale."""
        owner = value.__objclass__
        self._feed("slot", str(owner.__module__), owner.__qualname__, value.__name__)
        self._class(owner)

    def _compiled(self, value: object, home: ModuleType | None) -> None:
        """C-written functions and descriptors — enter the name, and unless they are standard library or builtins, add the sha256 of the extension module's file."""
        owner = _compiled_owner(value)
        qualname = value.__qualname__ if isinstance(value, _COMPILED_CALLABLES) else ""
        self._feed("compiled", owner or "", str(qualname))
        if owner is None:
            self._fold(home)
        elif not _named_only(owner):
            self._fold(sys.modules.get(owner))


@dataclass(frozen=True)
class GlobalName:
    """The module global name the value was looked up by (to cover an unwalkable value by that name's source statement)."""

    module: ModuleType
    name: str


@dataclass(frozen=True)
class _SlotPlace:
    """The location of one function the walk met for the first time — the function number (in the order of :attr:`_Walker._seen`), the function's code and contents digest, and whether it is code that reads
    a function's contents as a value (:func:`_reads_code`)."""

    node: int
    slot: CodeSlot
    reads_code: bool


#: Names that read a function's contents as a value (attribute reads, global names) — they read the code, defaults, docstring, argument shape, and annotations of a function that is not called. When a function
#: having such a name is one whose contents are covered (executed at expansion, or not narrowed), cover all the contents of the closure's functions (the contents of a function that is not executed affect the expansion result).
#: Also read as a string constant (the name in ``getattr(f, "__code__")``). However, ``__doc__`` is looked at only in the name table — the string constant
#: "__doc__" in expansion code is a marker for writing a docstring into the expansion result (the same as :data:`_TEXT_SKIP`).
_CODE_READ_NAMES = frozenset(
    {
        "__code__",
        "__defaults__",
        "__kwdefaults__",
        "__closure__",
        "__doc__",
        "__signature__",
        "__text_signature__",
        "__annotations__",
        "__annotate__",
        "signature",
        "getsource",
        "getsourcelines",
        "findsource",
        "getfullargspec",
        "getdoc",
        "getcomments",
        "getclosurevars",
        "getcallargs",
        "get_annotations",
        "get_type_hints",
        "get_instructions",
        "Bytecode",
        "code_info",
        "show_code",
        # Things in the standard library that read annotations (not walked inside — when met by name, they get a per-annotation digest).
        "singledispatch",
        "singledispatchmethod",
        "register",
        "validate_call",
        "validate_arguments",
        "TypeAdapter",
    }
)
_CODE_READ_TEXTS = _CODE_READ_NAMES - {"__doc__"}


def _reads_code(facts: CodeFacts) -> bool:
    """Whether the function's code reads a function's contents as a value (:data:`_CODE_READ_NAMES`)."""
    return not (
        _CODE_READ_NAMES.isdisjoint(facts.names) and _CODE_READ_TEXTS.isdisjoint(facts.texts)
    )


def _reads_code_by_alias(namespace: Mapping[str, object], names: tuple[str, ...]) -> bool:
    """Whether the original name of a function that the function calls by global name is a name that reads a function's contents (:data:`_CODE_READ_NAMES`) — for the alias of ``from inspect import signature as sig``,
    only ``sig`` appears in the name table."""
    return any(
        isinstance(value := namespace.get(name), (FunctionType, types.BuiltinFunctionType, types.MethodType))
        and getattr(value, "__name__", None) in _CODE_READ_NAMES
        for name in names
    )


def _bits(indexes: Iterator[int]) -> int:
    """The number made by turning a sequence of indexes into bits (for index i, bit i is 1)."""
    return functools.reduce(operator.or_, (1 << index for index in indexes), 0)


def _frozen_dataclass_instance(value: object) -> bool:
    """Whether it is a frozen dataclass value (not a class)."""
    if isinstance(value, type) or not dataclasses.is_dataclass(value):
        return False
    params = vars(type(value)).get("__dataclass_params__")
    return isinstance(params, dataclasses._DataclassParams) and params.frozen


def _public_kind(member: object) -> tuple[str, ...]:
    """The kind of one attribute of an extension class (method, descriptor, value — a plain value by its value text, otherwise by the type's name)."""
    match member:
        case (
            FunctionType()
            | staticmethod()
            | classmethod()
            | types.BuiltinFunctionType()
            | types.MethodDescriptorType()
            | types.WrapperDescriptorType()
            | types.ClassMethodDescriptorType()
            | types.MethodWrapperType()
        ):
            return ("method",)
        case types.GetSetDescriptorType() | types.MemberDescriptorType() | property():
            return ("descriptor",)
        case _ if _plain(member):
            return ("value", _plain_text(member))
        case _:
            kind = type(member)
            return ("value-type", kind.__module__, kind.__qualname__)


def _initializing(module: ModuleType) -> bool:
    """Whether the module is in the middle of an import (the marker importlib sets on the spec — true until the body finishes executing)."""
    spec = vars(module).get("__spec__")
    return spec is not None and vars(spec).get("_initializing") is True


#: Memory of whether the name of a module under a package is found by the import search (within the process — module name -> whether found).
_SUBMODULES: dict[str, bool] = {}


def _submodule(module: ModuleType, name: str) -> str | None:
    """If the module is a package and the name is a module under it, that module's name (independent of whether it is loaded — only modules under a loaded package
    appear as attributes, so deciding by attribute would make the digest of the same macro change with the process's import order)."""
    if "__path__" not in vars(module) or not name.isidentifier():
        return None
    full = f"{module.__name__}.{name}"
    value = vars(module).get(name)
    if isinstance(value, ModuleType):
        return full if value.__name__ == full else None
    found = _SUBMODULES.get(full)
    if found is None:
        try:
            found = (
                importlib.util.find_spec(full) is not None
            )  # the body is not executed (the package is already loaded)
        except (ImportError, ValueError):
            found = False
        _SUBMODULES[full] = found
    return full if found and name not in vars(module) else None


#: Module attributes excluded from the candidates for string-constant names — the module's docstring. The "__doc__" in expansion code is a marker for writing a docstring onto the function or class of the expansion
#: result, and the module's docstring does not affect the expansion result (if entered, a one-line change to the docstring of doeff_hy's record_runtime
#: made all users of defhandler be judged stale).
_TEXT_SKIP = frozenset({"__doc__"})

#: Attributes that merely name a module's location (the paths of the source and bytecode files that the import machinery puts in) — whether met as a global name, a module attribute, or a
#: string-constant name, only a marker is entered, not the value. The value changes with the working tree's location and does not change
#: the behavior of code with the same contents (records are carried over to another tree and verified — #2598). If entered, the daily full verification, with doeff placed in a different dir each run,
#: had the digest of the post-processing of the type-check expansion (the static_check project — `module.__file__ = ...` puts __file__ in the name table) change
#: every run, and every saved expansion missed each time. The spelling of the path does not cover the contents of the file at the path, so dropping it does not reduce what is covered
#: (the module's code and values are covered by the rules that follow them by contents).
#: Code that reads a location as a value (a global read, an attribute read, a string-constant name) or a binding statement may read the file it leads to (neighboring data, stubs), but
#: the walk does not cover those contents — as with environment variable reads, make it an unverifiable marker (_note_location,
#: blind review B). Merely writing to a module you made (STORE_ATTR) is not a read.
_LOCATION_NAMES = frozenset({"__file__", "__cached__"})


def _text_attribute(module: ModuleType, text: str) -> bool:
    """Whether to enter a string-constant-named attribute — one that is in the module's namespace and is neither the module's docstring (:data:`_TEXT_SKIP`) nor a module under a package
    (whether a module under a package appears as an attribute changes with whether it was loaded — if it is a name in the name table, :func:`_submodule` covers it by
    file)."""
    if text in _TEXT_SKIP or text not in vars(module):
        return False
    value = vars(module)[text]
    return not (isinstance(value, ModuleType) and value.__name__ == f"{module.__name__}.{text}")


def _compiled_owner(value: object) -> str | None:
    """The module name of the owner of a C-written function or descriptor (None if unknown)."""
    match value:
        case (
            types.MethodDescriptorType()
            | types.WrapperDescriptorType()
            | types.ClassMethodDescriptorType()
            | types.GetSetDescriptorType()
            | types.MemberDescriptorType()
        ):
            return value.__objclass__.__module__
        case types.MethodWrapperType():
            return type(value.__self__).__module__
        case types.BuiltinFunctionType():
            owner = value.__module__
            if isinstance(owner, str):
                return owner
            bound = value.__self__
            return bound.__name__ if isinstance(bound, ModuleType) else type(bound).__module__
        case _:
            return None


def _python_source(module: ModuleType | None) -> bool:
    """Whether a module's classes can be followed by their contents (bases, method bodies) — false only for extension modules (the file ends with any of the standard
    importlib.machinery.EXTENSION_SUFFIXES) and modules with no file. Do not decide by a copy of source extensions: when Hy's extension was added to the
    definition point (code_store.HY_SOURCE_SUFFIXES), forgetting to fix the copy covered that module's classes by public shape only and missed a change to a method body.
    An unknown suffix errs toward following by contents (the safe side)."""
    file = None if module is None else vars(module).get("__file__")
    return isinstance(file, str) and not file.endswith(tuple(importlib.machinery.EXTENSION_SUFFIXES))


def _import_target(reference: ImportReference, package: str | None) -> str:
    """The module name an import instruction reads (if relative, resolved from the function's package — empty if it cannot be resolved). Does not import."""
    if reference.level <= 0:
        return reference.name
    if not package:
        return ""
    try:
        return importlib.util.resolve_name("." * reference.level + reference.name, package)
    except ImportError:
        return ""


def source_text(data: object, path: str) -> str | bytes | None:
    """The compiled source text — the bytes / str that were passed, or what was read from the file (None if it cannot be read)."""
    if isinstance(data, (str, bytes)):
        return data
    try:
        with io.open_code(path) as source:
            return source.read()
    except OSError:
        return None
