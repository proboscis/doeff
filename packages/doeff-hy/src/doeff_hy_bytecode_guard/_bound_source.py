"""Reading that covers one module global by the source statements that bind and mutate its name, instead of the whole file.

When the macro closure digest (:mod:`doeff_hy_bytecode_guard._macro_use`) meets a global whose contents cannot be put in by value (dict, list, an untraceable
object), it used to cover it with the sha256 of the module's file. Here the module source is read, and for that name:

- Top-level statements (outside function and class definitions) that touch the name: statements that bind it (assignment, import) and statements that mutate it (subscript assignment,
  ``.update``, ``.append``, ``del``, re-assignment; the same inside top-level for/if/try). A statement is included in canonical form with positions removed
  (``ast.dump`` for Python, ``hy.repr`` for Hy); the names the statement reads (:attr:`Statement.loads`) are followed by the walker.
- Of top-level function and class definitions, those touching the name in parts evaluated at import time (decorators, defaults, annotations, bases, class body)
  are included as statements. Those that may mutate the name in a body run at call time (:attr:`Binding.mutators`) have that function
  (or class) followed by the walker, so its code enters the digest even for registration functions called at import time or functions not reached from the closure.
- Statements that bind the name by import (:attr:`Binding.imports`) are followed by the walker into the imported module with the same rules. Hy
  ``(import ...)`` is resolved to the same shape as a Python import (``(import a.b)`` binds a, ``(import a.b :as n)`` binds a.b,
  ``(import a.b [x :as y])`` binds x of a.b. Failure case: covering by the file without resolving this
  missed a change in a table the imported module built from an environment variable).

When unreadable (a module without source, a read error, a Hy star import or an unreadable import form), it returns ``None`` or :attr:`Binding.unresolved`,
and the walker covers by the whole file to stay on the safe side. The body of a Hy definition (defn etc.) is not split into import time and call time; the definition
form touching the name is included as a statement.

:func:`source_names` reads all names appearing in a module's source (likewise a pure read). When a value the walker cannot trace is covered by its owner's file, it
is the material for judging whether the owner's source reads a value decided outside the file (an environment variable).

The reading is a pure computation determined only by the source contents and the name (and the way of reading: this module's code, the Python version, the extension, the Hy version and hy.repr registrations),
so in addition to an in-process memo it is looked up by contents in the store (kind ``.binding`` of :mod:`doeff_hy_bytecode_guard._code_store`) and remembered.
A fresh process's check had been re-parsing the same source every time (about 2/3 of the check walk at import of the checker).
Unreadable files (OSError) are not remembered. A read of source that cannot be parsed (None) is determined by contents, so it is remembered.
"""

import ast
import io
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

# Definition point of Hy source suffixes (a light module that reads only the standard library; loader_hooks also reads it at startup).
from doeff_hy_bytecode_guard._code_store import HY_SOURCE_SUFFIXES

#: In-process memo of contents (file path -> mtime, size, contents).
_SOURCES: dict[str, "SourceFile"] = {}

#: In-process memo of parse results (file path -> (parsed contents, parse result)).
_PARSED: dict[str, tuple["SourceFile", "PythonModule | HyModule | None"]] = {}

#: In-process memo of bindings read ((file path, global name) -> (contents read, binding)).
_BINDINGS: dict[tuple[str, str], tuple["SourceFile", "Binding | None"]] = {}

#: Methods that do not mutate the value itself when called (e.g. ``name.get(...)``). The result is treated as a value obtained from the name (it may point at inner values), and its destination
#: is judged by tracing back with the same rules.
_READ_ONLY_METHODS = frozenset(
    {"get", "keys", "values", "items", "copy", "count", "index", "__contains__", "__getitem__"}
)

#: Built-in calls that do not let the argument reference escape (the result is a number or bool, e.g. ``len(name)``). ``list``, ``sorted`` etc. return a container
#: pointing at inner values, so they are not included (the destination may mutate them).
_READ_ONLY_CALLS = frozenset({"len", "bool", "isinstance"})

#: Heads of Hy definitions (included as a statement with the whole body form) and heads that bind a name.
#: (doeff-hy val and var also bind the left name of a pair as a module global.)
_HY_SETTERS = frozenset({"setv", "setx", "val", "var"})
_HY_IMPORTS = frozenset({"import"})


@dataclass(frozen=True)
class Statement:
    """One source statement: its canonical form with positions removed, the names it reads, and the attribute names appearing in it (attributes included when the read name is a module)."""

    text: str
    loads: tuple[str, ...]
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class ImportedName:
    """One import that binds the name (name of the imported module, relative level, name of the attribute in the imported module; empty if it is the module itself)."""

    module: str
    level: int
    name: str


@dataclass(frozen=True)
class Binding:
    """What the source says about one global.

    statements = top-level statements touching the name; mutators = names of top-level definitions that may mutate the name in their body; imports = imports that
    bind the name; bound = whether a top-level statement binds the name (excluding imports); unresolved = there is an untraceable binding (a Hy import).
    """

    statements: tuple[Statement, ...]
    mutators: tuple[str, ...]
    imports: tuple[ImportedName, ...]
    bound: bool
    unresolved: bool


@dataclass(frozen=True)
class PythonModule:
    """Parsed Python source (a sequence of top-level statements)."""

    body: tuple[ast.stmt, ...]


@dataclass(frozen=True)
class HyModule:
    """Parsed Hy source (a sequence of top-level forms)."""

    forms: tuple[object, ...]


@dataclass(frozen=True)
class SourceFile:
    """File state (mtime, size) and the bytes of the contents read at that time; the unit of the in-process memo."""

    mtime_ns: int
    size: int
    data: bytes


def binding_of(path: str, name: str) -> Binding | None:
    """Read the binding and mutation of global ``name`` from the module file's source (None if unreadable; once per file state
    within a process, once per source contents across processes)."""
    source = _source(path)
    if source is None:
        return None
    remembered = _BINDINGS.get((path, name))
    if remembered is not None and remembered[0] is source:
        return remembered[1]
    binding = _stored_binding(source, path, name)
    _BINDINGS[(path, name)] = (source, binding)
    return binding


#: Memo of names appearing in source (in process: file path -> (contents read, all names)).
_NAMES: dict[str, tuple["SourceFile", "tuple[str, ...] | None"]] = {}


def source_names(path: str) -> tuple[str, ...] | None:
    """All names appearing in the module file's source: for Python, names, attribute names, and each part of import modules and aliases; for Hy, each part of symbols
    (including mangled names and each part of dotted expressions). Used to judge, when a value the walker cannot trace is covered by its owner's file, whether the owner's source reads a value decided
    outside the file (an environment variable). None if the source is unreadable or cannot be parsed. Read once per file state within a process (called only when the walker
    meets an untraceable value; not remembered in the store)."""
    source = _source(path)
    if source is None:
        return None
    remembered = _NAMES.get(path)
    if remembered is not None and remembered[0] is source:
        return remembered[1]
    names = _names_in(_parsed(source, path))
    _NAMES[path] = (source, names)
    return names


def _names_in(parsed: "PythonModule | HyModule | None") -> tuple[str, ...] | None:
    """All names in the source read (in name order)."""
    match parsed:
        case None:
            return None
        case PythonModule(body=body):
            return tuple(
                sorted(
                    {
                        name
                        for statement in body
                        for node in ast.walk(statement)
                        for name in _python_node_names(node)
                    }
                )
            )
        case HyModule(forms=forms):
            return tuple(
                sorted(
                    {
                        part
                        for form in forms
                        for symbol in _hy_symbols(form)
                        for part in _hy_names(symbol)
                    }
                )
            )


def _python_node_names(node: ast.AST) -> tuple[str, ...]:
    """Names of one Python clause (not looking inside): name reads and writes, attribute names, and each part of import modules and aliases."""
    match node:
        case ast.Name(id=found) | ast.Attribute(attr=found):
            return (found,)
        case ast.alias(name=found, asname=alias):
            return (*found.split("."), *(() if alias is None else (alias,)))
        case ast.ImportFrom(module=str() as module):
            return tuple(module.split("."))
        case _:
            return ()


def _source(path: str) -> SourceFile | None:
    """File contents (read once per file state within a process; None if unreadable)."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    remembered = _SOURCES.get(path)
    if (
        remembered is not None
        and remembered.mtime_ns == status.st_mtime_ns
        and remembered.size == status.st_size
    ):
        return remembered
    try:
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError:
        return None
    source = SourceFile(status.st_mtime_ns, status.st_size, data)
    _SOURCES[path] = source
    return source


def _stored_binding(source: SourceFile, path: str, name: str) -> Binding | None:
    """Binding of global ``name`` -- if the store (kind BINDING_SUFFIX of :mod:`doeff_hy_bytecode_guard._code_store`) has a read of the same source contents,
    use it; otherwise parse the source, read it, and add it to the store. The read is a pure computation determined only by the source contents, the name (and the Python and Hy versions and this module's code),
    so it may be looked up by contents across processes, worktrees, and versions (live values are not remembered; the walker follows them each time)."""
    entry = _binding_entry(source, path, name)
    if entry is not None:
        stored = _read_binding_entry(entry)
        if not isinstance(stored, _Missing):
            return stored
    binding = _binding_in(_parsed(source, path), name)
    if entry is not None:
        _write_binding_entry(entry, binding)
    return binding


def _binding_in(parsed: "PythonModule | HyModule | None", name: str) -> Binding | None:
    """Read the binding and mutation of global ``name`` from the source already read."""
    match parsed:
        case None:
            return None
        case PythonModule(body=body):
            return _python_binding(body, name)
        case HyModule(forms=forms):
            return _hy_binding(forms, name)


def _parsed(source: SourceFile, path: str) -> PythonModule | HyModule | None:
    """Parse the file contents (once per file state within a process). None if it is not Python/Hy source or cannot be parsed."""
    remembered = _PARSED.get(path)
    if remembered is not None and remembered[0] is source:
        return remembered[1]
    parsed = _read(source.data, path)
    _PARSED[path] = (source, parsed)
    return parsed


def _read(data: bytes, path: str) -> PythonModule | HyModule | None:
    """Parse source (Python by ast, Hy by Hy's reader; None if unreadable, e.g. due to your own reader macros). The path is used only for the kind (extension) and the error
    message (the parse result is determined by contents alone)."""
    try:
        # Same reading as opening the file as text (UTF-8, newline normalization).
        text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8").read()
    except UnicodeDecodeError:
        return None
    if path.endswith(".py"):
        try:
            return PythonModule(tuple(ast.parse(text, filename=path).body))
        except (SyntaxError, ValueError):
            return None
    if path.endswith(_HY_SUFFIXES):
        import hy  # only when Hy source is encountered
        from hy.errors import HyError

        try:
            return HyModule(tuple(hy.read_many(text, filename=path)))
        except (HyError, SyntaxError, ValueError):
            return None
    return None


# ---- Store binding entry ------------------------------------------------------------------------------------------------


#: Mark of the binding entry key (bump the trailing number when the entry shape changes). A change to the reading itself (this module's code) is covered by
#: the sha256 of this module's file (:func:`_own_digest`) that goes into the key.
BINDING_TAG = "doeff-hy/bound-source/1"

#: Hy source suffixes (a copy of the definition point in code_store is not kept, so that when a suffix is added the reading and the key kind change together).
_HY_SUFFIXES = HY_SOURCE_SUFFIXES

#: The Hy module that defines hy.repr and its registration table (Hy loads it lazily when ``hy.repr`` is accessed).
_HY_REPR_MODULE = "hy.core.hy_repr"


@dataclass(frozen=True)
class _Missing:
    """Mark meaning there is no usable store entry (to distinguish from an entry whose content None = source whose binding is unreadable)."""


@dataclass(frozen=True)
class OwnCode:
    """sha256 of this module file's contents (None if unreadable: the reading code is unknown, so the store is not used)."""

    sha256: str | None


#: Memo of this module's code (once per process; only one key, "own").
_OWN: dict[str, OwnCode] = {}


def _own_digest() -> str | None:
    """sha256 of this module file's contents -- so a version that changed the reading code does not pull the previous version's reads from the store (None if unreadable)."""
    known = _OWN.get("own")
    if known is None:
        known = _OWN.setdefault("own", _read_own_code())
    return known.sha256


def _read_own_code() -> OwnCode:
    """Read this module's file and compute the sha256."""
    import hashlib  # only when the store is used

    try:
        with open(__file__, "rb") as stream:
            return OwnCode(hashlib.sha256(stream.read()).hexdigest())
    except OSError:
        return OwnCode(None)


def _binding_entry(source: SourceFile, path: str, name: str) -> str | None:
    """Path of the binding entry (None if the store is configured off, this module's code is unreadable, or the shape of Hy statements may change with process registrations).
    Key = mark, this module's code, the whole Python version (ast shape), source kind (the reading differs by extension), Hy reading and statement shape
    (:func:`_hy_reading`), name, and source contents."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # only when the store is used

    store = code_store.store_dir()
    own = None if store is None else _own_digest()
    if store is None or own is None:
        return None
    hy_source = path.endswith(_HY_SUFFIXES)
    reading = _hy_reading() if hy_source else ""
    if reading is None:
        return None
    kind = "hy" if hy_source else "py" if path.endswith(".py") else "other"
    parts = (BINDING_TAG, own, sys.version, kind, reading, name)
    return code_store.entry_path(
        store, code_store.content_key(parts, source.data), code_store.BINDING_SUFFIX
    )


def _hy_reading() -> str | None:
    """What decides the Hy source reading and statement shape (``hy.repr``): a fingerprint of the Hy version and the hy.repr registration table (the in-process shared table). None when the table
    has registered functions other than Hy's own and built-ins (the code of those functions cannot be covered by the key, so the store is not used). Hy's reading
    (``hy.read_many``) always uses a fresh reader and does not depend on the process's reader macros.

    If the module that owns the table (:data:`_HY_REPR_MODULE`) is not yet loaded, the table is still Hy's default (the registration entry point hy.repr_register also
    loads the same module), so the key uses the default mark and the module is not loaded just to look at the table (accessing hy.repr makes Hy load it, and only the check
    paid 0.055 to 0.094 s). When the store has nothing, the hy.repr in :func:`_hy_binding` builds statements
    with the default table it loads, so the contents of an entry with the default mark are the reading with the default table."""
    from types import FunctionType  # only when Hy source is encountered

    import hy

    if _HY_REPR_MODULE not in sys.modules:
        return f"{hy.__version__}\0default"
    # The hy.repr registration table is a global of the Hy module that defines hy.repr (not visible to the type checker, so it is fetched from the globals of the hy.repr function).
    # If the table shape differs, the way of reading is unknown.
    shown = hy.repr
    registry = shown.__globals__.get("_registry") if isinstance(shown, FunctionType) else None
    if not isinstance(registry, dict):
        return None
    rows = tuple(sorted(_repr_row(kind, entry) for kind, entry in registry.items()))
    if any(not row.function.startswith(("hy.", "builtins.")) for row in rows):
        return None
    return f"{hy.__version__}\0{rows!r}"


@dataclass(frozen=True, order=True)
class ReprRow:
    """One row of the hy.repr registration table (type, registered function, name of the nesting mark) -- to build the table fingerprint."""

    kind: str
    function: str
    placeholder: str


def _repr_row(kind: object, entry: object) -> ReprRow:
    """Write a registration table row by name (a row whose function pair has a different shape is written as a row with unreadable functions; it is not counted as Hy's own function)."""
    kind_name = f"{getattr(kind, '__module__', '')}.{getattr(kind, '__qualname__', '')}"
    match entry:
        case (function, placeholder):
            name = f"{getattr(function, '__module__', '')}.{getattr(function, '__qualname__', '')}"
            return ReprRow(kind_name, name, repr(placeholder))
        case _:
            return ReprRow(kind_name, f"unreadable:{type(entry).__qualname__}", "")


def _read_binding_entry(entry: str) -> "Binding | None | _Missing":
    """Read a store binding entry (_Missing if absent). An entry of the wrong shape is removed by name and treated as _Missing (the caller re-parses and re-adds it)."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # only when the store is used

    data = code_store.read_entry(entry)
    if data is None:
        return _Missing()
    import marshal  # only when hit

    try:
        value = marshal.loads(data)
    except (EOFError, ValueError, TypeError) as error:
        code_store.discard_entry(entry, f"unreadable by marshal: {error}")
        return _Missing()
    binding = _binding_from_parts(value)
    if isinstance(binding, _Missing):
        code_store.discard_entry(entry, "binding has the wrong shape")
    return binding


def _write_binding_entry(entry: str, binding: Binding | None) -> None:
    """Add the binding read to the store (with writes configured off, only the named store; a store that cannot be written is
    silently skipped -- the store exists only for speed). Same rule as loader_hooks for adding code."""
    from doeff_hy_bytecode_guard import _code_store as code_store  # only when the store is used

    if sys.dont_write_bytecode and not code_store.store_is_named():
        return
    import marshal  # only when adding

    code_store.write_entry(entry, marshal.dumps(_binding_parts(binding)))


#: Shape of a store entry's contents (marshal boundary: field names and types are held by :class:`Binding`; here only the order for writing and reading).
BindingParts = (
    tuple[
        tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...],
        tuple[str, ...],
        tuple[tuple[str, int, str], ...],
        bool,
        bool,
    ]
    | None
)


def _binding_parts(binding: Binding | None) -> BindingParts:
    """Turn a binding into a tuple that marshal can write (None stays as is: unreadable source). Inverse of :func:`_binding_from_parts`."""
    if binding is None:
        return None
    return (
        tuple((s.text, s.loads, s.attributes) for s in binding.statements),
        binding.mutators,
        tuple((i.module, i.level, i.name) for i in binding.imports),
        binding.bound,
        binding.unresolved,
    )


def _binding_from_parts(value: object) -> "Binding | None | _Missing":
    """Build a binding from the tuple read from the store (_Missing if the shape is wrong)."""
    match value:
        case None:
            return None
        case (
            tuple() as statements,
            tuple() as mutators,
            tuple() as imports,
            bool() as bound,
            bool() as unresolved,
        ):
            read_statements = tuple(_statement_from_parts(row) for row in statements)
            read_imports = tuple(_import_from_parts(row) for row in imports)
            names = _names_of(mutators)
            if (
                any(row is None for row in read_statements)
                or any(row is None for row in read_imports)
                or names is None
            ):
                return _Missing()
            return Binding(
                tuple(row for row in read_statements if row is not None),
                names,
                tuple(row for row in read_imports if row is not None),
                bound,
                unresolved,
            )
        case _:
            return _Missing()


def _statement_from_parts(row: object) -> Statement | None:
    """One statement row of a binding entry (None if the shape is wrong)."""
    match row:
        case (str() as text, tuple() as loads, tuple() as attributes):
            read_loads = _names_of(loads)
            read_attributes = _names_of(attributes)
            if read_loads is None or read_attributes is None:
                return None
            return Statement(text, read_loads, read_attributes)
        case _:
            return None


def _import_from_parts(row: object) -> ImportedName | None:
    """One import row of a binding entry (None if the shape is wrong)."""
    match row:
        case (str() as module, int() as level, str() as name):
            return ImportedName(module, level, name)
        case _:
            return None


def _names_of(values: tuple[object, ...]) -> tuple[str, ...] | None:
    """Read the sequence from the entry as a sequence of names (strings) (None if any is not a name)."""
    names = tuple(value for value in values if isinstance(value, str))
    return names if len(names) == len(values) else None


# ---- Python ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementFacts:
    """What one top-level statement says about one global (statements to include, names of definitions that may mutate in their body, imports that bind, whether it binds)."""

    statement: Statement | None
    mutator: str | None
    imports: tuple[ImportedName, ...]
    binds: bool


def _python_binding(body: tuple[ast.stmt, ...], name: str) -> Binding:
    """Read the binding and mutation of global ``name`` from Python top-level statements."""
    facts = tuple(_python_statement_facts(statement, name) for statement in body)
    return Binding(
        statements=tuple(fact.statement for fact in facts if fact.statement is not None),
        mutators=tuple(fact.mutator for fact in facts if fact.mutator is not None),
        imports=tuple(imported for fact in facts for imported in fact.imports),
        bound=any(fact.binds for fact in facts),
        unresolved=False,
    )


def _python_statement_facts(statement: ast.stmt, name: str) -> StatementFacts:
    """Read one top-level statement: a definition is split into the parts evaluated at import time and the body; other statements are taken whole."""
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            named = statement.name == name
            touched = named or any(_names(node, name) for node in _import_time_nodes(statement))
            mutates = any(_mutates(node, name) for node in _call_time_nodes(statement))
            return StatementFacts(
                _python_statement(statement) if touched else None,
                statement.name if mutates else None,
                (),
                named,
            )
        case _:
            found = _imports_binding(statement, name)
            touched = bool(found) or _mentions(statement, name)
            return StatementFacts(
                _python_statement(statement) if touched else None,
                None,
                found,
                _binds(statement, name),
            )


def _python_statement(statement: ast.stmt) -> Statement:
    """Canonical form of one statement (``ast.dump`` with positions removed) and the names and attribute names it reads."""
    loads = sorted({node.id for node in ast.walk(statement) if isinstance(node, ast.Name)})
    attributes = sorted(
        {node.attr for node in ast.walk(statement) if isinstance(node, ast.Attribute)}
    )
    return Statement(ast.dump(statement), tuple(loads), tuple(attributes))


def _mentions(node: ast.AST, name: str) -> bool:
    """Whether the clause (and its insides) touches the name (name read/write, ``global``, import binding)."""
    return any(_names(inner, name) for inner in ast.walk(node))


def _names(node: ast.AST, name: str) -> bool:
    """Whether the clause itself (not looking inside) touches the name."""
    match node:
        case ast.Name(id=found):
            return found == name
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            return name in names
        case ast.alias():
            return _alias_binds(node, name)
        case _:
            return False


def _alias_binds(alias: ast.alias, name: str) -> bool:
    """Whether one import alias binds the name."""
    bound = alias.asname if alias.asname is not None else alias.name.partition(".")[0]
    return bound == name


def _binds(statement: ast.stmt, name: str) -> bool:
    """Whether the statement binds the name (name writes in assignment, for, with, del), outside inner function definitions."""
    return any(
        isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, (ast.Store, ast.Del))
        for node in _outside_functions(statement)
    ) or any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
        for node in _outside_functions(statement)
    )


def _imports_binding(statement: ast.stmt, name: str) -> tuple[ImportedName, ...]:
    """Imports inside the statement (outside inner function definitions) that bind the name."""
    return tuple(
        imported
        for node in _outside_functions(statement)
        for imported in _imported_names(node, name)
    )


def _imported_names(node: ast.AST, name: str) -> tuple[ImportedName, ...]:
    """The alias that one import statement binds (``import a.b`` binds a, ``import a.b as n`` binds a.b)."""
    match node:
        case ast.Import(names=aliases):
            return tuple(
                ImportedName(
                    alias.name if alias.asname is not None else alias.name.partition(".")[0], 0, ""
                )
                for alias in aliases
                if _alias_binds(alias, name)
            )
        case ast.ImportFrom(module=module, level=level, names=aliases):
            return tuple(
                ImportedName(module or "", level, alias.name)
                for alias in aliases
                if _alias_binds(alias, name)
            )
        case _:
            return ()


def _outside_functions(node: ast.AST, top: bool = True) -> Iterator[ast.AST]:
    """The clause and clauses inside it (does not enter function definition and lambda bodies; definition names, decorators, and defaults are entered)."""
    yield node
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef() if not top:
            inner: Iterable[ast.AST] = _function_import_time(node)
        case ast.Lambda():
            inner = (*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None))
        case _:
            inner = ast.iter_child_nodes(node)
    for child in inner:
        yield from _outside_functions(child, top=False)


def _function_import_time(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.AST, ...]:
    """Parts of a function definition evaluated at import time (decorators, defaults, annotations)."""
    arguments = function.args
    every = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *((arguments.vararg,) if arguments.vararg is not None else ()),
        *((arguments.kwarg,) if arguments.kwarg is not None else ()),
    )
    return (
        *function.decorator_list,
        *arguments.defaults,
        *(default for default in arguments.kw_defaults if default is not None),
        *(argument.annotation for argument in every if argument.annotation is not None),
        *((function.returns,) if function.returns is not None else ()),
    )


def _import_time_nodes(
    definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.AST]:
    """All clauses of a top-level definition evaluated at import time (for a class, including the body statements; does not enter function and lambda bodies)."""
    match definition:
        case ast.ClassDef():
            for root in (*definition.decorator_list, *definition.bases, *definition.keywords):
                yield from _outside_functions(root)
            for statement in definition.body:
                match statement:
                    case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                        yield from _import_time_nodes(statement)
                    case _:
                        yield from _outside_functions(statement)
        case _:
            for root in _function_import_time(definition):
                yield from _outside_functions(root)


def _call_time_nodes(
    definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> list[ast.AST]:
    """Parts of a top-level definition that run when called (function bodies; for a class, method bodies)."""
    match definition:
        case ast.ClassDef():
            return [
                node
                for statement in definition.body
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for node in _call_time_nodes(statement)
            ]
        case _:
            return list(definition.body)


def _mutates(node: ast.AST, name: str) -> bool:
    """Whether the clause (a body run at call time) may mutate the name: a ``global`` declaration, or a use of the name whose value destination does not end where the reference does not escape
    (:func:`_stays`). Mutation through an alias (top-level ``ALIAS = name``) is covered because the statement binding the alias is included as touching the name, and
    the walker follows the alias that statement binds with the same rules."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    for inner in ast.walk(node):
        match inner:
            case ast.Global(names=names) | ast.Nonlocal(names=names) if name in names:
                return True
            case ast.Name(id=found) if found == name and not (
                isinstance(inner.ctx, ast.Load) and _stays(inner, parents)
            ):
                # A name write (assignment, del) may be a local name without global, but is counted as a mutation to stay on the safe side.
                return True
    return False


def _stays(value: ast.expr, parents: dict[int, ast.AST]) -> bool:
    """Whether the destination of a value obtained from the name (a name read, a subscript read of it, the result of a read-only method, an attribute read) ends where the reference does not escape.
    The result of a subscript read, a read-only method, an attribute read, or the branches of or / and / a conditional expression is traced back by the same rules.
    Only these are accepted as read-only at the end: comparison, the test of if / while / conditional expression / assert, not, arguments of :data:`_READ_ONLY_CALLS`, and
    expression statements that discard the value. Right side of assignment, return, yield, arguments of other calls, the source of for and comprehension iteration (elements are bound and
    may be mutated), writes to subscripts or attributes, and calls of non-read-only methods are counted as possible mutation (safe side)."""
    parent = parents.get(id(value))
    match parent:
        case ast.Attribute(value=inner, attr=attribute, ctx=ast.Load()) if inner is value:
            return _attribute_stays(parent, attribute, parents)
        case _ if _passes_through(value, parent):
            return _stays(parent, parents)
        case _:
            return _ends_inside(value, parent)


def _passes_through(value: ast.expr, parent: ast.AST | None) -> bool:
    """Whether the enclosing clause passes the value (or an inner value) through as its result as is (subscript read, or / and, conditional expression branches)."""
    match parent:
        case ast.Subscript(value=inner, ctx=ast.Load()):
            return inner is value
        case ast.IfExp(test=test):
            return test is not value
        case ast.BoolOp():
            return True
        case _:
            return False


def _ends_inside(value: ast.expr, parent: ast.AST | None) -> bool:
    """Whether the enclosing clause finishes using the value without letting the reference escape (comparison, not, test, argument of a read-only built-in, discarded expression statement)."""
    match parent:
        case ast.Compare() | ast.UnaryOp(op=ast.Not()) | ast.Expr():
            return True
        case (
            ast.If(test=test) | ast.While(test=test) | ast.Assert(test=test) | ast.IfExp(test=test)
        ):
            return test is value
        case ast.Call(func=ast.Name(id=called), args=args, keywords=()):
            return called in _READ_ONLY_CALLS and any(argument is value for argument in args)
        case _:
            return False


def _attribute_stays(attribute: ast.Attribute, name: str, parents: dict[int, ast.AST]) -> bool:
    """Attribute read of a value obtained from the name: for a read-only method call, trace the result; for a non-call read (``value.field``), trace the field value.
    Any other method call (``.append``, ``.update`` ...) may mutate."""
    match parents.get(id(attribute)):
        case ast.Call(func=func) as call if func is attribute:
            return name in _READ_ONLY_METHODS and _stays(call, parents)
        case _:
            return _stays(attribute, parents)


# ---- Hy ----------------------------------------------------------------------------------------------------------


def _hy_binding(forms: tuple[object, ...], name: str) -> Binding:
    """Read the binding and mutation of global ``name`` (mangled name) from Hy top-level forms. All forms touching the name are included as statements
    (definition forms too: the body is not split into import time and call time). Forms that bind by import are resolved to the imported module, and unresolvable forms (star
    import, unreadable forms) are not followed (unresolved)."""
    import hy

    touched = tuple(
        (form, symbols)
        for form in forms
        for symbols in (tuple(_hy_symbols(form)),)
        if any(_hy_names(symbol)[0] == name for symbol in symbols)
    )
    imported = tuple(
        _hy_import_clauses(list(form)[1:], name)
        for form, _ in touched
        if _hy_head(form) in _HY_IMPORTS
    )
    return Binding(
        statements=tuple(
            # Like a Python import statement, an import form has no names it reads (the binding is followed via imports; the names of siblings that the same form imports
            # are not followed as names it reads).
            Statement(hy.repr(form), (), ())
            if _hy_head(form) in _HY_IMPORTS
            else Statement(
                hy.repr(form),
                tuple(sorted({_hy_names(symbol)[0] for symbol in symbols})),
                tuple(sorted({part for symbol in symbols for part in _hy_names(symbol)[1:]})),
            )
            for form, symbols in touched
        ),
        mutators=(),
        imports=tuple(found for clauses in imported if clauses is not None for found in clauses),
        bound=any(_hy_binds(form, name) for form, _ in touched),
        unresolved=any(clauses is None for clauses in imported),
    )


@dataclass(frozen=True)
class HyImportedName:
    """One entry of a Hy import name list ``[x :as y]``: the attribute name in the imported module and the name it binds (both mangled)."""

    imported: str
    bound: str


def _hy_import_clauses(items: list[object], name: str) -> tuple[ImportedName, ...] | None:
    """Read the arguments of ``(import ...)`` from the front and return the imports that bind the name: after the module (a symbol or dotted expression), one of ``:as alias``, a name list, or
    nothing following (binds the top package). A star import or an unreadable form gives None (the caller treats it as an untraceable binding)."""
    from hy.models import Keyword, List, Symbol

    if not items:
        return ()
    module = _hy_module_name(items[0])
    if module is None:
        return None
    match items[1:]:
        case [Keyword() as keyword, Symbol() as alias, *after] if keyword.name == "as":
            here = (ImportedName(module.name, module.level, ""),) if _hy_names(alias) == (name,) else ()
        case [List() as listed, *after]:
            names = _hy_import_names(list(listed))
            if names is None:
                return None
            here = tuple(
                ImportedName(module.name, module.level, entry.imported)
                for entry in names
                if entry.bound == name
            )
        case [Symbol() as star, *_] if str(star) == "*":
            return None
        case after if module.level == 0:
            top = module.name.partition(".")[0]
            here = (ImportedName(top, 0, ""),) if top == name else ()
        case _:
            return None  # relative import with neither a name list nor an alias (the shape of Hy's reading is unknown)
    rest = _hy_import_clauses(list(after), name)
    return None if rest is None else (*here, *rest)


@dataclass(frozen=True)
class HyModuleName:
    """The name of the Hy import module (mangled dotted sequence) and the relative level."""

    name: str
    level: int


def _hy_module_name(item: object) -> HyModuleName | None:
    """The module part of an import: a symbol (``os``) or a dotted expression that Hy's reader builds (``a.b`` is ``(. a b)``, relative ``..h`` is
    ``(.. None h)``). None for an unreadable form."""
    import hy
    from hy.models import Expression, Symbol

    match item:
        case Symbol() if str(item) != "*":
            return HyModuleName(".".join(_hy_names(item)), 0)
        case Expression() if len(item) >= 2 and all(isinstance(part, Symbol) for part in item):
            head, *parts = (str(part) for part in item)
            relative = parts[0] == "None"
            names = parts[1:] if relative else parts
            if set(head) != {"."} or not names:
                return None
            return HyModuleName(".".join(map(hy.mangle, names)), len(head) if relative else 0)
        case _:
            return None


def _hy_import_names(items: list[object]) -> tuple[HyImportedName, ...] | None:
    """Read an import name list ``[x y :as z]`` (None for an unreadable form)."""
    from hy.models import Keyword, Symbol

    match items:
        case []:
            return ()
        case [Symbol() as imported, Keyword() as keyword, Symbol() as alias, *rest] if (
            keyword.name == "as"
        ):
            here = HyImportedName(_hy_names(imported)[0], _hy_names(alias)[0])
        case [Symbol() as imported, *rest]:
            here = HyImportedName(_hy_names(imported)[0], _hy_names(imported)[0])
        case _:
            return None
    tail = _hy_import_names(list(rest))
    return None if tail is None else (here, *tail)


def _hy_head(form: object) -> str:
    """Head name of a form (empty if it is not an expression)."""
    from hy.models import Expression, Symbol

    if isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Symbol):
        return str(form[0])
    return ""


def _hy_binds(form: object, name: str) -> bool:
    """Whether the form binds the name (the left of a ``(setv name ...)`` pair, the name of a definition such as ``(defn name ...)``; imports excluded)."""
    from hy.models import Expression, Symbol

    if not isinstance(form, Expression):
        return False
    match _hy_head(form):
        case head if head in _HY_SETTERS:
            return any(
                isinstance(target, Symbol) and _hy_names(target)[0] == name
                for target in list(form)[1::2]
            )
        case head if head in _HY_IMPORTS:
            return False  # import binding is in Binding.imports (not counted in bound, same as Python bindings)
        case head if head.startswith("def"):
            return any(
                isinstance(item, Symbol) and _hy_names(item)[0] == name for item in list(form)[1:3]
            )
        case _:
            return False


def _hy_symbols(form: object) -> Iterator[object]:
    """Symbols in the form (including nested sequences)."""
    from hy.models import Sequence, Symbol

    match form:
        case Symbol():
            yield form
        case Sequence():
            for item in form:
                yield from _hy_symbols(item)
        case _:
            pass


def _hy_names(symbol: object) -> tuple[str, ...]:
    """Mangled names of the parts of a symbol (``a.b.c`` is a, b, c: the first is the global name, the rest are attribute names)."""
    import hy

    text = str(symbol)
    parts = text.split(".") if not text.startswith(".") else [text]
    return tuple(hy.mangle(part) for part in parts if part)
