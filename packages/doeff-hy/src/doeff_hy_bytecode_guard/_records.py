"""Shape of the macro-dependency record and the checking logic (pure functions -- files and ``sys.modules`` are received as arguments).

The record is a tuple placed at the end of the constants of a Hy module's code object:

    ("doeff-hy/macro-dependencies/7", <Hy version>,
     ((<table name>, <provider module name>, <macro name>, <digest, None, or marker>, <marker of covered code>), ...),   # macros the expansion used
     (<names looked up but not found>, ...),                                     # names absent from the table (stale if a macro of the same name is added later)
     (<provider module names that put macros into the table>, ...),
     ((<module name>, <attribute name>, <digest, None, or marker>), ...),        # values outside macros that change the expansion result
     ((<module name>, <file sha256>), ...))                                      # module files the closure depended on

The digest is the digest of the closure of the macro function's code (how it is built and checked is in :mod:`doeff_hy_bytecode_guard._macro_use`). When the closure walk
hits a module that is mid-import or cannot be imported, the digest is None (the material then depends on how far the import got) -- that row is
not compared by digest but by the file of the module the closure depended on. When the closure walk hits a read of an environment variable, the marker :data:`ENVIRONMENT_READ`
is used (it cannot be checked -- the check always judges it stale). No file path is kept -- at check time it is looked up again from the module name in the current environment.
The digest of a used macro names, among the functions of the closure, the functions whose contents (code) it covered, by a covered-code marker (the hex of the bit set
of numbers in the order the closure walk met the functions -- the functions run by that module's expansion, plus the functions that are not narrowed). The check rebuilds the digest of the current closure with the same marker and compares.

Because it is a code object constant, it goes into the .pyc as is through marshal, and the .pyc keeps its standard shape (PEP 552 header + marshal body).
The value does not change even if the image-build bytecode tool (agora-controllers' deploy/bytecode.py) rewrites the body.

The Hy gensym names in compiled code are renumbered to sequential numbers within the module by :func:`canonical_gensyms`.
"""

import hashlib
import importlib.util
import marshal
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import CodeType
from typing import TypeVar

#: Tag of the record (raise the trailing number when the shape changes -- a record of the old shape is treated the same as "no record" and recompiled).
#: 2 = a record of code whose gensym names are canonicalized. The code of a record 1 has gensym numbers decided by compile order,
#: so it is made to recompile once. The tag of the code key in the store (code_store.CODE_TAG) is not raised: an entry of record 1 is judged to have no record when looked up and is not used (loader_hooks._from_shared_store), and the recompiled code overwrites the entry under the same key, so old-generation
#: files do not remain in the store (raising the key tag would leave old-generation files that nobody overwrites). The cost of looking up and discarding is once per module.
#: 3 = from a record per file the expansion depended on to a record per macro the expansion actually used (the digest of the closure of the macro function's code). A record 2 has a
#: different shape, so it is treated as "no record" and recompiled once (the reason for not raising the key tag is the same as 2).
#: 4 = a digest that covers untraceable values in the closure narrowly instead of the whole file (a global table covers the source statements that bind and rewrite the name; an extension class covers its name and
#: public shape; a ContextVar covers its name and default). A record 3 digest never matches when rebuilt with the current method, so it would be judged stale once even without raising,
#: but the generations of shape are separated by the record tag (the reason for not raising the key tag is the same as 2).
#: 5 = a digest built from mid-import state is not put into the record (None); the file rows are widened from the module that could not be imported to all
#: modules the closure depended on, and compared only when some row cannot be compared by digest (the digest in the middle of a circular import
#: changed with the import order of the process, and processes with different orders kept overwriting each other's store entries). The file rows of a record 4 have a
#: different meaning, so they are treated as "no record" (the reason for not raising the key tag is the same as 2).
#: 6 = the used-macro rows gain the marker of the closure functions whose contents (code) the digest covers, and the code the digest covers is narrowed to the functions
#: run by that module's expansion plus the functions that are not narrowed (the head of a binding statement, and the closure of code that reads a function's contents as a value) (
#: a change to binding_forms._Walker._assign, which is not called by the defk expansion, had rebuilt 1,197 users of defk). A record 5 has a different row
#: shape, so it is treated as "no record" (the reason for not raising the key tag is the same as 2).
#: 7 = a record that also includes in the used macros the macros brought in by a require inside a function (macros looked up from the compiler's local table).
#: The row shape is the same as 6, but a record 6 does not cover local macros -- the check only checks rows that exist in the record,
#: so the .pyc and store entries of a record 6 kept being judged up to date even when a local macro changed (blind review A of the design verification). Generations with different
#: coverage are separated by the record tag, and a record 6 is treated as "no record" (the reason for not raising the key tag is the same as 2).
#: From key tag 3 of the store, code with a different record sits as a separate entry in the same key dir (it is not overwritten -- the record's fingerprint is
#: the entry's name). So after the record tag is raised, old-generation entries remain un-overwritten, and if unused they disappear at the per-key entry count limit or in the 7-day cleanup
#: (the reason for not raising the key tag becomes: raising the key tag would make even entries that do not depend on the record all miss).
RECORD_TAG = "doeff-hy/macro-dependencies/7"

#: The names of the dicts Hy puts in a module's namespace (the table of ordinary macros, the table of reader macros, and the table of public macro names that Hy's export writes), and
#: the prefix of names Hy assigns. The single home of Hy's internal names is this module; users (macro_use, doeff_effect_analyzer._result_cache,
#: doeff_hy._static_baseline, doeff_hy._static_stub) do not hold the name strings and look them up through the constants and functions below (:func:`macro_tables`,
#: :func:`exported_macro_names`, :func:`fold_anon_var_numbers`) -- so there is one place to fix when Hy renames, and a change that copies a name string into another file turns the scan in
#: packages/doeff-hy/tests/test_hy_internal_names_have_one_home.py red.
#: The names, and the fact that Hy's expansion looks up the table with ``name in table`` and ``table[name]`` (the premise on which macro_use.RecordingTable remembers looked-up names and names absent from the table), are
#: Hy's internal shape, measured by a check against the Hy version in uv.lock -- the checks that exercise the wrapper (the macro and
#: reader macro change checks in packages/doeff-hy/tests/test_bytecode_guard.py, and the check for when a macro is added under a name the user side calls), and the checks that apply the functions below to a real Hy module
#: (test_records_reads_... and test_the_temporaries_hy_names_... in the same file) -- both selected by a change to uv.lock.
#: If the names change or the table is looked up with ``.get``, the wrapper fails to record the macros it used and silently keeps using a stale expansion.
MACRO_TABLE = "_hy_macros"
READER_TABLE = "_hy_reader_macros"
EXPORT_NAMES = "_hy_export_macros"
GENERATED_NAME_PREFIX = "_hy_"
#: The prefix of the name doeff-hy's open-form gives the bind it releases in place (``hy.gensym("bound")`` -- the shape GENSYM_NAME reads,
#: ``_hy_gensym_bound_<counter>``). doeff_effect_analyzer tells the release ``(b := None)`` of a ``(! e)`` name by this prefix.
RELEASED_BIND_PREFIX = GENERATED_NAME_PREFIX + "gensym_bound_"

#: The key for the macro table in the local state Hy's compiler opens for function bodies and the like (the dict that ``HyASTCompiler.new_local_state`` pushes onto ``local_state_stack``).
#: A require or defmacro inside a function puts macros into this table, and Hy's expansion looks it up before the module's table, so
#: a macro looked up from here does not pass through the module's table (macro_use.RecordingTable.local_tables remembers it). The entry name and key are Hy's internal shape,
#: measured against the Hy version in uv.lock by the wrapper's check (the check for a macro changed by a require inside a function in test_bytecode_guard.py -- selected by a change to uv.lock).
#: If Hy starts opening local state without going through this entry, the wrapper fails to record macros from a require inside a function and silently
#: uses a stale expansion (when the key changes it is not silent: compile fails with a KeyError from the wrapped entry).
LOCAL_MACRO_TABLE = "macros"

#: The header of a PEP 552 .pyc (4 bytes of magic, 4 bytes of flags, 8 bytes of mtime and size or source hash).
PYC_HEADER_BYTES = 16
FLAG_HASH_BASED = 0b01
FLAG_CHECK_SOURCE = 0b10

#: The substitute for a digest (the closure walk hit a read of an environment variable, or code that reads a module's location as a value -- the digest copies neither the value of the environment variable nor
#: the contents of files reached from the location, so it cannot be checked). A marker different from both a hex sha256 and
#: None (mid-import); the check always judges this row stale (the user re-expands in every process).
#: The record tag (RECORD_TAG) is not raised: the previous version's check compares this marker against the current digest and
#: judges it stale, and the previous version's record becomes a marker with the current walk, so it is judged stale (both just re-expand -- raising it would make every Hy module
#: recompile once).
ENVIRONMENT_READ = "environment-read"

#: The shape of the marker of the closure functions whose contents (code) a used macro's digest covers -- the hex of a number whose bits are the numbers in the order the closure walk first met the functions
#: (covering the function numbered i sets bit i to 1. How it is built and read is in macro_use). Rows with no digest (None, ENVIRONMENT_READ) get "0".
COVERED = re.compile(r"[0-9a-f]+")


@dataclass(frozen=True)
class UsedMacro:
    """One macro the expansion used -- the name of the table looked up (``_hy_macros`` / ``_hy_reader_macros``), the name of the module that defined the macro, its
    name in that module's table, the digest of the closure of the macro function's code (None when, at record time, the closure walk hit a module that was mid-import or
    could not be imported -- compared by the record's file rows; :data:`ENVIRONMENT_READ` when it hit a read of an environment variable), and
    the marker of the closure functions whose contents the digest covers (:data:`COVERED`)."""

    table: str
    module: str
    name: str
    digest: str | None
    covered: str


@dataclass(frozen=True)
class ValueReference:
    """Where a value outside macros that changes the expansion result lives (module name and attribute name) -- the post-processing of the type-check expansion (``POST_PROCESSING`` of doeff_hy._static_check).
    It is looked up by module attribute, so even if a check swaps another function of the module, this value's digest does not change."""

    module: str
    name: str


@dataclass(frozen=True)
class UsedValue:
    """One value outside macros that changes the expansion result -- its location (module name, attribute name) and the digest of the value's closure (None means the same as in
    :class:`UsedMacro`)."""

    module: str
    name: str
    digest: str | None


@dataclass(frozen=True)
class UsedFile:
    """One module the closure depended on (the owner of something the walk traced, or the import target inside the body of a macro that could not be imported) -- the module name and the
    sha256 of its file at record time. Compared only when some row cannot be compared by digest (the record or the current walk hit a module mid-import).
    At check time the module is not imported; the current environment's file is looked up from the module name and compared."""

    module: str
    sha256: str


@dataclass(frozen=True)
class WholeRequire:
    """One form in which the user required all of a provider's macros (``(require m *)`` or ``(require m :as p)``) -- the name of the provider (the
    module that defined the macros) and the prefix of names in the user's table (empty for an all-require with no prefix). A require that selects names is not recorded: the user's table
    holds only the selected names, so whatever is later added to the provider does not change the expansion."""

    module: str
    prefix: str


@dataclass(frozen=True)
class MacroRecord:
    """What the expansion of one Hy module depended on.

    - ``hy_version``: the Hy version (Hy's own macros and functions are covered by the version).
    - ``macros``: the macros the expansion used.
    - ``absent``: names looked up in the expanding module's table and not found (function calls, Hy core macros). If any of the ``providers`` later gains a macro of the same
      name, that call turns into a macro expansion.
    - ``providers``: providers whose macros the user required in full (a require that selects names is not included).
    - ``values``: values outside macros that change the expansion result.
    - ``files``: the files of the modules the closure of ``macros`` and ``values`` depended on (compared only when some row cannot be compared by digest -- not
      imported at check time).
    """

    hy_version: str
    macros: tuple[UsedMacro, ...]
    absent: tuple[str, ...]
    providers: tuple[WholeRequire, ...]
    values: tuple[UsedValue, ...]
    files: tuple[UsedFile, ...]


def record_of(code: CodeType) -> MacroRecord | None:
    """Read the record carried on code (None when absent or of a different shape -- the caller recompiles)."""
    if not code.co_consts:
        return None
    match code.co_consts[-1]:
        case (str() as tag, str() as hy_version, macros, absent, providers, values, files) if (
            tag == RECORD_TAG
        ):
            return record_from_parts(
                RecordParts(hy_version, macros, absent, providers, values, files)
            )
        case _:
            return None


@dataclass(frozen=True)
class RecordParts:
    """The fields for writing a record at a boundary (code constants, the JSON of a store file) -- inside the fields are only strings and sequences of sequences of strings. When reading back,
    the shape inside the fields has not been checked yet, so they are ``object`` (:func:`record_from_parts` checks)."""

    hy_version: str
    macros: object
    absent: object
    providers: object
    values: object
    files: object


def record_parts(record: MacroRecord) -> RecordParts:
    """Turn a record into fields that can be written to code constants and store files -- the inverse of :func:`record_from_parts`."""
    return RecordParts(
        record.hy_version,
        tuple((row.table, row.module, row.name, row.digest, row.covered) for row in record.macros),
        record.absent,
        tuple((row.module, row.prefix) for row in record.providers),
        tuple((row.module, row.name, row.digest) for row in record.values),
        tuple((row.module, row.sha256) for row in record.files),
    )


def record_from_parts(parts: RecordParts) -> MacroRecord | None:
    """Assemble a record from fields read at a boundary (None if a field has a different shape -- the caller treats it the same as no record)."""
    macros = _each(parts.macros, _used_macro)
    absent = _each(parts.absent, _name)
    providers = _each(parts.providers, _whole_require)
    values = _each(parts.values, _used_value)
    files = _each(parts.files, _used_file)
    if macros is None or absent is None or providers is None or values is None or files is None:
        return None
    return MacroRecord(parts.hy_version, macros, absent, providers, values, files)


def record_to_json(record: MacroRecord) -> list[object]:
    """Turn a record into a value writable as JSON (the boundary of the store file -- the store of the type-check expansion). The inverse of :func:`record_from_json`."""
    parts = record_parts(record)
    return [parts.hy_version, parts.macros, parts.absent, parts.providers, parts.values, parts.files]


def record_from_json(value: object) -> MacroRecord | None:
    """Assemble a record from a value read from JSON (None if the shape differs -- the caller does not use the store)."""
    match value:
        case [str(hy_version), macros, absent, providers, values, files]:
            return record_from_parts(
                RecordParts(hy_version, macros, absent, providers, values, files)
            )
        case _:
            return None


def record_fingerprint(record: MacroRecord) -> str:
    """Fingerprint of a record's contents (the name of a store entry -- the analyzer's expanded tree and the type-check expansion store share this one). Because the record
    carries no file path, expansions in different work trees that used the same macros get the same fingerprint."""
    import json  # imported only when building the name of a store entry

    text = json.dumps(record_to_json(record), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


_Row = TypeVar("_Row")


def _each(rows: object, read: Callable[[object], "_Row | None"]) -> tuple[_Row, ...] | None:
    """Read each row of a sequence (tuple or list) with read (None if it is not a sequence or any row cannot be read)."""
    if not isinstance(rows, (tuple, list)):
        return None
    read_rows = tuple(map(read, rows))
    return None if any(item is None for item in read_rows) else read_rows


def _name(value: object) -> str | None:
    """One name field of a record (None if not a string)."""
    return value if isinstance(value, str) else None


#: Used-macro rows already read in this process (the row of the code constants -> the read row). The same rows recur in most Hy modules
#: (the rows of defk, val and the like differ only in the covered marker), and matching each row's shape again was about a third of reading
#: the records of the 3,000 modules the agora-controllers declaration tool loads. Only tuple rows are kept (rows read from JSON are lists, not hashable).
_USED_MACROS: dict[tuple[object, ...], "UsedMacro"] = {}


def _whole_require(row: object) -> WholeRequire | None:
    """One all-require row of a record (None if the shape differs)."""
    match row:
        case [str(module), str(prefix)]:
            return WholeRequire(module, prefix)
        case _:
            return None


def _used_macro(row: object) -> UsedMacro | None:
    """One used-macro row of a record (None if the shape differs) -- a tuple row read before is taken from :data:`_USED_MACROS`."""
    if type(row) is not tuple:
        return _used_macro_of(row)
    known = _USED_MACROS.get(row)
    if known is None:
        known = _used_macro_of(row)
        if known is not None:
            _USED_MACROS[row] = known
    return known


def _used_macro_of(row: object) -> UsedMacro | None:
    """Match the shape of one used-macro row (None if the shape differs)."""
    match row:
        case [str(table), str(module), str(name), digest, str(covered)] if (
            digest is None or isinstance(digest, str)
        ) and COVERED.fullmatch(covered):
            return UsedMacro(table, module, name, digest, covered)
        case _:
            return None


def _used_value(row: object) -> UsedValue | None:
    """One row of a record for a value outside macros (None if the shape differs)."""
    match row:
        case [str(module), str(name), digest] if digest is None or isinstance(digest, str):
            return UsedValue(module, name, digest)
        case _:
            return None


def _used_file(row: object) -> UsedFile | None:
    """One row of a record for a module covered per file (None if the shape differs)."""
    match row:
        case [str(module), str(sha256)]:
            return UsedFile(module, sha256)
        case _:
            return None


def with_record(code: CodeType, record: MacroRecord) -> CodeType:
    """Return code with the record added at the end of its constants (replacing it if a record is already there).

    Because it is only appended at the end, the existing constant indexes (the ``LOAD_CONST`` arguments) do not move and the meaning of execution does not change.
    """
    constants = code.co_consts[:-1] if record_of(code) is not None else code.co_consts
    parts = record_parts(record)
    return code.replace(
        co_consts=(
            *constants,
            (
                RECORD_TAG,
                parts.hy_version,
                parts.macros,
                parts.absent,
                parts.providers,
                parts.values,
                parts.files,
            ),
        )
    )


def macro_tables(namespace: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """The macro tables Hy put in a module's namespace -- the table of ordinary macros (:data:`MACRO_TABLE`) and the table of reader macros (:data:`READER_TABLE`), in
    that order, only those present in the namespace and being a dict (an empty tuple if it is not a Hy module). The namespace is ``vars(module)``: tables are read by
    name from the namespace, not as attributes (even if an object placed in ``sys.modules`` in place of a module computes attributes, that code is not run)."""
    return tuple(
        table
        for name in (MACRO_TABLE, READER_TABLE)
        if isinstance(table := namespace.get(name), dict)
    )


def exported_macro_names(namespace: Mapping[str, object]) -> frozenset[str]:
    """The names of macros that a provider's namespace puts into the user's table with Hy's ``(require provider *)`` -- of the names in the macro table (:data:`MACRO_TABLE`),
    the names in the table of names written by Hy's export (:data:`EXPORT_NAMES`) if it exists, otherwise the names not starting with ``_`` (the same reading as ``source_exports`` of
    ``hy.macros.require`` in Hy 1.3.1. Even if the export has a name absent from the table, Hy puts in only the names present in the table)."""
    entries = namespace.get(MACRO_TABLE)
    names = frozenset(entries) if isinstance(entries, dict) else frozenset[str]()
    exports = namespace.get(EXPORT_NAMES)
    if isinstance(exports, (list, tuple)):
        return frozenset(str(name) for name in exports) & names
    return frozenset(name for name in names if not name.startswith("_"))


#: The shape of a Hy gensym name (``hy.core.util.gensym`` in Hy 1.3.1 = ``_hy_gensym_<mangled base>_<counter>``). The counter is one per process and
#: advances on every gensym call -- even for the same source, the number changes depending on what was compiled first (another module, whether a require target's .pyc exists, another thread).
#: The trailing digits of the name are the counter.
GENSYM_NAME = re.compile(r"(_hy_gensym_.*_)([0-9]+)")

#: A name for which Hy rewrote a binding (``_hy_<kind>_<original name>_<number>`` of ``HyASTCompiler.get_anon_var`` -- bindings of let and except). If the original name is a gensym
#: name, its counter is embedded in the name (the let of absent-as = ``_hy_let__hy_gensym_absent_token_<counter>_<number>``). The last <number> is the per-compile
#: counter (decided within the module), so it is not renamed.
#: The two shapes are Hy's internal shape, measured by a check that drives the compile entry with the PyPI Hy of the uv.lock version (the
#: gensym counter check in packages/doeff-hy/tests/test_bytecode_guard.py -- selected by a change to uv.lock).
RENAMED_BINDING = re.compile(r"(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)")

#: The shape Hy assigns to temporary names and let / except binding names (``HyASTCompiler.get_anon_var`` = ``_hy_<kind>[_<original name>]_<counter>`` -- ``_hy_anon_<counter>`` when match or if is used as a
#: value, ``_hy_let_<original name>_<counter>`` for let). The last <counter> is a sequence number within one compile, and adding a single definition
#: shifts the numbers of the names after it. The first group is the name without the number (even when the original name ends in digits like ``x1`` or ``x_1``, only the last
#: ``_<counter>`` is the number -- a non-greedy head and a trailing ``\b``). For a binding that has a gensym name inside the original name (:data:`RENAMED_BINDING`), only
#: the trailing counter is folded; the embedded gensym counter is the job of the gensym renumbering (:func:`renamed`). The shape is measured by a check that compiles a Hy module of the uv.lock version
#: and collects the names (test_the_temporaries_hy_names_... in test_bytecode_guard.py).
ANON_VAR_NAME = re.compile(r"\b(" + re.escape(GENERATED_NAME_PREFIX) + r"[A-Za-z0-9_]*?)_[0-9]+\b")


def canonical_gensyms(code: CodeType) -> CodeType:
    """Return code in which the gensym names in the code tree of a Hy module are renamed, renumbered from 1 in order of counter within the module -- so that the code of
    the same source is the same regardless of compile order, process, and thread (the fingerprint of a packed Program, the .pyc, and the contents of the shared
    store used to change depending on which process compiled what first).

    It collects the gensym names that appear in the name tables (locals, cells, frees, global and attribute names, function names and qualname) and in string constants (keyword names of calls,
    annotation keys), renumbers them from ``_hy_gensym_<base>_1`` in ascending counter order, and replaces them everywhere at once. The replacement is one-to-one, so
    the uniqueness of names within the module is preserved. The order of gensym calls within a module does not depend on compile order, so the renumbered names do not
    depend on it either. Hy's counter itself is not touched (so it does not collide with concurrent compiles)."""
    names = gensym_renames(_texts(code))
    if all(old == new for old, new in names.items()):
        return code
    return _rewritten(code, names)


def gensym_renames(texts: Iterable[str]) -> dict[str, str]:
    """From the gensym names that appear in a sequence of names and strings, build the mapping to names renumbered from ``_hy_gensym_<base>_1`` in ascending counter order --
    so that the code tree (:func:`canonical_gensyms`) and the tree of the type-check expansion (doeff_hy._static_check) use the same one
    renumbering. The mapping is keyed by the gensym name itself (including those embedded in binding names Hy rewrote), and the replacement is done by
    :func:`renamed`."""
    found: dict[str, re.Match[str]] = {}
    for text in texts:
        match = _gensym_match(text)
        if match is not None:
            found[match.group(0)] = match
    ordered = sorted(found.values(), key=lambda match: int(match.group(2)))
    return {match.group(0): f"{match.group(1)}{rank}" for rank, match in enumerate(ordered, 1)}


def unread_gensym_texts(code: CodeType) -> tuple[str, ...]:
    """Names and string constants in the code tree that contain a gensym name but that :func:`canonical_gensyms` cannot read (such as names embedded in a statement) --
    they remain outside canonicalization and their numbers change with compile order. So that a check confirms this is empty and, if an unreadable embedding appears in a macro, fails naming it."""
    return tuple(
        sorted(
            {text for text in _texts(code) if "_hy_gensym_" in text and _gensym_match(text) is None}
        )
    )


def _gensym_match(text: str) -> "re.Match[str] | None":
    """The match of a gensym name inside one name (the name itself, or one embedded in a binding name Hy rewrote -- None if none)."""
    whole = GENSYM_NAME.fullmatch(text)
    if whole is not None:
        return whole
    renamed = RENAMED_BINDING.fullmatch(text)
    return None if renamed is None else _gensym_match(renamed.group(2))


def _texts(code: CodeType) -> Iterator[str]:
    """List the name tables and string constants of code and its nested code (every place a gensym name can appear)."""
    yield from (*code.co_varnames, *code.co_cellvars, *code.co_freevars, *code.co_names)
    yield code.co_name
    yield from code.co_qualname.split(".")
    for constant in code.co_consts:
        yield from _constant_texts(constant)


def _constant_texts(value: object) -> Iterator[str]:
    """List the strings inside one constant (inside tuples and sets too) and the names of nested code."""
    match value:
        case CodeType():
            yield from _texts(value)
        case str():
            yield value
        case tuple() | frozenset():
            for item in value:
                yield from _constant_texts(item)
        case _:
            return


def renamed(text: str, names: Mapping[str, str]) -> str:
    """The renumbered name for one name's gensym name (for a binding name Hy rewrote, only the embedded gensym name is replaced)."""
    replaced = names.get(text)
    if replaced is not None:
        return replaced
    binding = RENAMED_BINDING.fullmatch(text)
    if binding is None:
        return text
    return binding.group(1) + renamed(binding.group(2), names) + binding.group(3)


def fold_anon_var_numbers(text: str, mark: str) -> str:
    """Replace the numbers of temporary names Hy assigned (:data:`ANON_VAR_NAME`) in text with mark (``_hy_anon_44`` -> ``_hy_anon_#``) -- numbers are decided by
    compile order, so that the wording of the same name is not counted as different things because of differing numbers (doeff_hy._static_baseline)."""
    return ANON_VAR_NAME.sub(lambda found: f"{found.group(1)}_{mark}", text)


def _rewritten(code: CodeType, names: Mapping[str, str]) -> CodeType:
    """Make a copy of code with the gensym names in the name tables and string constants of code and its nested code replaced by the names in names (the original code is not changed)."""
    return code.replace(
        co_varnames=tuple(renamed(name, names) for name in code.co_varnames),
        co_cellvars=tuple(renamed(name, names) for name in code.co_cellvars),
        co_freevars=tuple(renamed(name, names) for name in code.co_freevars),
        co_names=tuple(renamed(name, names) for name in code.co_names),
        co_name=renamed(code.co_name, names),
        co_qualname=".".join(renamed(part, names) for part in code.co_qualname.split(".")),
        co_consts=tuple(_rewritten_constant(constant, names) for constant in code.co_consts),
    )


def _rewritten_constant(value: object, names: Mapping[str, str]) -> object:
    """The value of one constant with gensym names replaced (strings, inside tuples and sets, nested code -- anything else as is)."""
    match value:
        case CodeType():
            return _rewritten(value, names)
        case str():
            return renamed(value, names)
        case tuple():
            return tuple(_rewritten_constant(item, names) for item in value)
        case frozenset():
            return frozenset(_rewritten_constant(item, names) for item in value)
        case _:
            return value


def timestamp_header_matches(header: bytes, *, mtime: int, size: int) -> bool:
    """Whether a timestamp-style .pyc header matches the source's modification time and size (the lower 32 bits, the same as Python's check) -- a hash-style one is
    false (leave the check to Python itself). So that the store is looked up only when the work tree's .pyc cannot be used."""
    if len(header) < PYC_HEADER_BYTES:
        return False
    flags = int.from_bytes(header[4:8], "little")
    if flags & FLAG_HASH_BASED:
        return False
    return header[8:12] == (mtime & 0xFFFFFFFF).to_bytes(4, "little") and header[
        12:16
    ] == (size & 0xFFFFFFFF).to_bytes(4, "little")


def python_checks_source(header: bytes, check_hash_based_pycs: str) -> bool:
    """Whether Python checks a .pyc with this header against the source before using it (true for the timestamp style and for checked-hash)."""
    if len(header) < PYC_HEADER_BYTES:
        return True
    flags = int.from_bytes(header[4:8], "little")
    if not flags & FLAG_HASH_BASED:
        return True
    if check_hash_based_pycs == "always":
        return True
    if check_hash_based_pycs == "never":
        return False
    return bool(flags & FLAG_CHECK_SOURCE)


def pyc_bytes(
    code: CodeType, *, source: bytes, mtime: int, size: int, previous_header: bytes | None
) -> bytes:
    """The .pyc of recompiled code -- built in the standard shape of the same style (timestamp or hash) as the .pyc that was read."""
    magic = importlib.util.MAGIC_NUMBER
    flags = (
        int.from_bytes(previous_header[4:8], "little")
        if previous_header is not None and len(previous_header) >= PYC_HEADER_BYTES
        else 0
    )
    if flags & FLAG_HASH_BASED:
        header = magic + flags.to_bytes(4, "little") + importlib.util.source_hash(source)
    else:
        header = (
            magic
            + (0).to_bytes(4, "little")
            + (mtime & 0xFFFFFFFF).to_bytes(4, "little")
            + (size & 0xFFFFFFFF).to_bytes(4, "little")
        )
    return header + marshal.dumps(code)
