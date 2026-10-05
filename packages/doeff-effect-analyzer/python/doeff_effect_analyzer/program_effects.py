"""The effects a doeff Program can perform, read from its source without running it.

Why this module exists (and not the name-matching Rust core for this job):

- An effect is any class that subclasses ``doeff_vm.EffectBase``, including the
  ones a project defines itself.  Whether a name denotes such a class is decided
  by importing the module that defines the Program and looking the name up —
  aliases, re-exports and ``Effect = EffectBase`` style assignments are then
  exact.  Nothing is run: no Program is executed, no handler is installed.
- Hy modules are read the way Hy reads them: forms are macro-expanded with Hy's
  own compiler (``defk``, ``<-`` and project macros that wrap them, such as a
  ``defservice`` that expands to ``defk``) into a Python AST.  The Hy analyzer in
  the Rust core has its own reader and cannot expand user macros.

What counts, starting from a Program function (``@do`` / ``defk`` / a plain
generator function):

- ``yield E(...)`` / ``yield from E(...)`` with ``E`` an ``EffectBase`` subclass →
  the effect ``E``.
- ``yield f(...)`` / ``yield from f(...)`` with ``f`` another Program function →
  ``f``'s effects, recorded with ``via`` = the call chain.  The arguments the
  caller passes are bound to ``f``'s parameters when they are known (a
  module-level value, a value bound further up, or a Program call) — so a
  Program that takes its foundation as a parameter is read with the
  foundation its caller passes (``bindings=`` gives the root's parameters).
- a function that is not a generator but ``return``s such a call (a Program
  factory) is followed the same way.
- a Program run under handlers the Program installs itself —
  ``with_handlers([h …], body)``, ``WithHandler(h, body)`` (what ``handle``
  expands to), ``h(body)`` for a handler value (what ``with-handler`` expands
  to) — is a *handled* Program: its effects go through those handlers (a handled
  effect is replaced by what the clause performs) and only the residual leaves.
  ``effect_types`` is the residual; ``handled`` keeps each scope.
- ``E(..., f(...), ...)`` where ``E`` is an effect and ``f`` a Program function →
  a *carried* Program (``Spawn``, ``Try``, ``RemoteJob`` …).  Whether a carrier
  runs its Program under the same handlers (``Spawn``) or elsewhere
  (``RemoteJob``) is the carrier's semantics, so carried Programs are reported
  separately with their own effect sets; callers choose which to fold in.
- ``E(..., fn, ...)`` where ``fn`` is a function filling a field ``E`` declares in
  ``__doeff_runs_carried__`` (``Traverse(f, items)`` — its handler calls ``f`` for each
  item) → a carried Program: ``fn``'s body, read where it was written.  The handler's
  ``effect.f(item)`` then adds nothing where the handler is read.
- doeff-vm control values (``Resume``, ``Transfer``, ``Pass`` …) are not effects.

Anything the reader cannot follow (a yielded local variable, a call whose target
is not importable) is reported in ``unresolved`` instead of being dropped, and a
handler whose clauses cannot be read is named in ``residual.unknown_handlers`` (it is
treated as handling nothing, so what it might absorb is still reported).
"""

import ast
import collections
import contextlib
import functools
import hashlib
import importlib
import inspect
import itertools
import os
import pickle
import sys
import tempfile
import types
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from doeff_effect_analyzer import env_places

if TYPE_CHECKING:
    # Read at run time only where a Hy module is expanded or its cached tree read.
    from doeff_hy_bytecode_guard.records import MacroRecord


@dataclass(frozen=True)
class Location:
    file: str
    line: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class EffectUse:
    """One place an effect is performed.  ``via`` = Program functions from the target down.

    ``forwarded`` = the place passes on the effect a handler clause received (``yield
    effect``): what leaves is the effect that arrived, not a new effect of ``effect`` (the
    class the clause was read for) — ``pass_through`` sends it on as the arriving class.
    """

    effect: type
    location: Location
    via: tuple[str, ...] = ()
    forwarded: bool = False

    @property
    def name(self) -> str:
        return qualified_name(self.effect)


@dataclass(frozen=True)
class CarriedProgram:
    """A Program handed to an effect or to another Program function.

    ``Spawn(child(...))`` / ``RemoteJob(task(...))``: ``carrier`` is the effect class.
    ``remote_job(task(...))`` (a helper Program that performs the carrier effect):
    ``carrier`` is that helper function.
    """

    carrier: Any
    program: "ProgramEffects"
    location: Location
    via: tuple[str, ...] = ()


@dataclass(frozen=True)
class Unresolved:
    """Something yielded or called that the reader could not follow."""

    reason: str
    text: str
    location: Location
    via: tuple[str, ...] = ()


class Basis(str, Enum):
    """How a handler's clauses were decided (kept in every answer)."""

    CLAUSES = "clauses"  # read from its isinstance / match branches
    DECLARED = "declared"  # from the marks __doeff_handles__ / __doeff_effects__
    UNREAD = "unread"  # neither — the handler may handle anything; coverage is unknown


@dataclass(frozen=True)
class Clause:
    handles: type
    emits: "ProgramEffects"
    location: Location


@dataclass(frozen=True)
class HandlerEffects:
    """What a handler handles (one clause per effect class) and how that was decided.

    ``performs`` = what the handler performs itself around the body it is placed over, not
    in answer to an effect — a body wrapper that answers nothing (declared with
    ``__doeff_handles__ = ()`` and ``__doeff_effects__``: e.g. it reads a start position,
    starts tasks and then runs the body).  Those effects go to the handlers outside it.
    """

    name: str
    clauses: tuple[Clause, ...] = ()
    unresolved: tuple[Unresolved, ...] = ()
    basis: Basis = Basis.UNREAD
    performs: "ProgramEffects | None" = None

    @property
    def handled(self) -> frozenset[type]:
        return frozenset(clause.handles for clause in self.clauses)

    @property
    def known(self) -> bool:
        """False when neither clauses nor declarations could be read (coverage is unknown)."""
        return self.basis is not Basis.UNREAD

    def to_dict(self) -> dict[str, Any]:
        return {
            "handler": self.name,
            "basis": self.basis.value,
            "clauses": [
                {
                    "handles": qualified_name(clause.handles),
                    "at": str(clause.location),
                    "emits": list(clause.emits.effect_names),
                }
                for clause in self.clauses
            ],
            "performs": list(self.performs.effect_names) if self.performs is not None else [],
            "unresolved": [
                {"reason": item.reason, "text": item.text, "at": str(item.location)}
                for item in self.unresolved
            ],
        }


@dataclass(frozen=True)
class Escape:
    """An effect that leaves a Program (or a handler stack) unhandled.

    ``by`` = the handler whose clause performed it; ``""`` = the Program itself.
    """

    effect: type
    use: EffectUse
    by: str = ""


@dataclass(frozen=True)
class Residual:
    """What is left of a Program's effects: the escapes, the handlers that could not be
    read on the way (each may have absorbed something — or nothing), and the places in
    the Program the reader could not follow (each may perform something unseen)."""

    escapes: tuple[Escape, ...] = ()
    unknown_handlers: tuple[str, ...] = ()
    unresolved: tuple[Unresolved, ...] = ()

    @property
    def effect_types(self) -> frozenset[type]:
        """The effect classes that escape."""
        return frozenset(escape.effect for escape in self.escapes)

    @property
    def effect_names(self) -> list[str]:
        """Qualified names of ``effect_types``, sorted (for reports)."""
        return sorted({qualified_name(effect) for effect in self.effect_types})


def _no_carrier(_carrier: Any) -> bool:
    """The default fold: carried Programs run elsewhere unless the caller says otherwise."""
    return False


def runs_where_performed(carrier: Any) -> bool:
    """Whether ``carrier`` is an effect class whose handler runs the Program it carries where
    the effect was performed — under the handlers around the performing site
    (``__doeff_runs_carried__``: ``Try`` / ``Local`` / ``Listen`` / ``Spawn`` /
    ``SqlTransaction``).  A closure check folds those carried Programs in (agora-redesign
    #1456); a carrier that declares nothing (a remote job) runs its Program elsewhere."""
    if not _is_effect_class(carrier):
        return False
    effects = importlib.import_module("doeff_core_effects.effects")
    return bool(effects.runs_carried_of(carrier))


@dataclass(frozen=True)
class HandledProgram:
    """A Program the Program runs under handlers it installs itself.

    ``handlers`` are outermost first (``with_handlers`` order).  ``program`` is
    what runs inside; ``residual_with`` is what leaves the scope.
    """

    handlers: tuple[HandlerEffects, ...]
    program: "ProgramEffects"
    location: Location
    via: tuple[str, ...] = ()

    def residual_with(self, include: Callable[[Any], bool]) -> Residual:
        """What leaves the scope: the inner Program's residual through ``handlers``."""
        return pass_through(self.program.residual_with(include), self.handlers, include)

    @property
    def residual(self) -> Residual:
        """What leaves the scope, carried Programs not folded in."""
        return self.residual_with(_no_carrier)


@dataclass(frozen=True)
class ProgramEffects:
    """A Program's effects.

    ``effects`` = the uses it performs directly (and through the Program
    functions it calls), outside any handler it installs; ``handled`` = the
    Programs it runs under its own handlers; ``carried`` = Programs handed to a
    carrier.  ``effect_types`` is what reaches the caller: the direct effects
    plus the residual of every handled scope (carried Programs are folded in
    only through ``effect_types_with``).
    """

    target: str
    effects: tuple[EffectUse, ...] = ()
    carried: tuple[CarriedProgram, ...] = ()
    unresolved: tuple[Unresolved, ...] = ()
    handled: tuple[HandledProgram, ...] = ()

    def residual_with(self, include: Callable[[Any], bool]) -> Residual:
        """What leaves this Program, folding in carried Programs whose carrier ``include`` accepts."""
        inner = [
            *(c.program.residual_with(include) for c in self.carried if include(c.carrier)),
            *(scope.residual_with(include) for scope in self.handled),
        ]
        return Residual(
            escapes=(
                *(Escape(use.effect, use) for use in self.effects),
                *(escape for part in inner for escape in part.escapes),
            ),
            unknown_handlers=tuple(
                dict.fromkeys(name for part in inner for name in part.unknown_handlers)
            ),
            unresolved=(*self.unresolved, *(item for part in inner for item in part.unresolved)),
        )

    @property
    def residual(self) -> Residual:
        """What reaches the caller, carried Programs not folded in."""
        return self.residual_with(_no_carrier)

    @property
    def effect_types(self) -> frozenset[type]:
        """The effect classes that reach the caller (the residual)."""
        return self.residual.effect_types

    @property
    def effect_names(self) -> list[str]:
        """Qualified names of ``effect_types``, sorted (for reports)."""
        return self.residual.effect_names

    def effect_types_with(self, include: Callable[[Any], bool]) -> frozenset[type]:
        """What leaves, plus the effects of carried Programs whose carrier ``include`` accepts."""
        return self.residual_with(include).effect_types

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "effects": sorted(self.effect_names),
            "uses": [
                {"effect": use.name, "at": str(use.location), "via": list(use.via)}
                for use in self.effects
            ],
            "handled": [
                {
                    "handlers": [handler.name for handler in scope.handlers],
                    "at": str(scope.location),
                    "via": list(scope.via),
                    "residual": scope.residual.effect_names,
                    "program": scope.program.to_dict(),
                }
                for scope in self.handled
            ],
            "carried": [
                {
                    "carrier": qualified_name(carried.carrier),
                    "at": str(carried.location),
                    "via": list(carried.via),
                    "program": carried.program.to_dict(),
                }
                for carried in self.carried
            ],
            "unresolved": [
                {
                    "reason": item.reason,
                    "text": item.text,
                    "at": str(item.location),
                    "via": list(item.via),
                }
                for item in self.unresolved
            ],
            "unknownHandlers": list(self.residual.unknown_handlers),
        }


def pass_through(
    inner: Residual,
    handlers: Sequence[HandlerEffects],
    include: Callable[[Any], bool] = _no_carrier,
) -> Residual:
    """Run ``inner`` through ``handlers`` (outermost first) from the innermost handler out.

    A handled effect is replaced by the effects its clause performs (they go to
    the handlers outside it); a clause keyed on a parent class also handles its
    subclasses (``isinstance`` semantics).  What a handler performs itself around the
    body (``HandlerEffects.performs``) leaves it the same way, toward the handlers
    outside it.  A handler that could not be read
    handles nothing here and is named in ``unknown_handlers``.  The result has one
    escape per effect class (the first place it was seen).  ``inner``'s unresolved
    places are kept, and so are the places a clause that answers here cannot follow
    (its ``via`` starts with the handler's name) — what such a place performs goes to
    the handlers outside, unseen.
    """
    pending: dict[type, Escape] = {}
    for escape in inner.escapes:
        pending.setdefault(escape.effect, escape)
    unknown = [*inner.unknown_handlers, *(h.name for h in handlers if not h.known)]
    unresolved = list(inner.unresolved)
    for handler in reversed(handlers):
        emitted: dict[type, Escape] = {}
        for effect in list(pending):
            clause = next((c for c in handler.clauses if issubclass(effect, c.handles)), None)
            if clause is None:
                continue
            del pending[effect]
            performed = clause.emits.residual_with(include)
            unknown.extend(performed.unknown_handlers)
            unresolved.extend(
                replace(item, via=(handler.name, *item.via)) for item in performed.unresolved
            )
            for escape in performed.escapes:
                sent = _forwarded_as(escape, effect)
                emitted.setdefault(sent.effect, replace(sent, by=escape.by or handler.name))
        if handler.performs is not None:
            own = handler.performs.residual_with(include)
            unknown.extend(own.unknown_handlers)
            unresolved.extend(replace(item, via=(handler.name, *item.via)) for item in own.unresolved)
            for escape in own.escapes:
                emitted.setdefault(escape.effect, replace(escape, by=escape.by or handler.name))
        for effect, escape in emitted.items():
            pending.setdefault(effect, escape)
    return Residual(
        tuple(pending.values()), tuple(dict.fromkeys(unknown)), tuple(dict.fromkeys(unresolved))
    )


def _forwarded_as(escape: Escape, arrived: type) -> Escape:
    """``escape`` as it leaves a clause that answered ``arrived``.

    The clause passing on the effect it received (``use.forwarded``, performed by the
    clause itself — ``by`` empty) sends on the effect that arrived: a clause keyed on a
    parent class (``EffectBase`` narrowed by ``:when``) forwards the concrete ``arrived``,
    answered further out as that class.  Any other escape leaves as it is.
    """
    if not escape.use.forwarded or escape.by or not issubclass(arrived, escape.effect):
        return escape
    return Escape(arrived, replace(escape.use, effect=arrived), escape.by)


def qualified_name(obj: Any) -> str:
    return f"{obj.__module__}.{obj.__qualname__}"


# --------------------------------------------------------------------------- source


@dataclass(frozen=True)
class _WholeTree:
    """A module tree held whole: a Python module, or a Hy module expanded in this process."""

    tree: ast.Module

    def find(self, function: types.FunctionType) -> "FunctionNode | None":
        """The def of ``function`` (see ``_find_function``)."""
        return _find_function(self.tree, function)


@dataclass(frozen=True)
class _ModuleSource:
    module: types.ModuleType
    definitions: "_WholeTree | _ChunkedTree"
    filename: str


_MODULE_CACHE: dict[str, _ModuleSource] = {}


def module_ast(module: types.ModuleType) -> ast.Module:
    """The module's AST; Hy modules are macro-expanded with Hy's compiler first."""
    return _module_source(module).definitions.tree


def _module_source(module: types.ModuleType) -> _ModuleSource:
    cached = _MODULE_CACHE.get(module.__name__)
    if cached is not None and cached.module is module:
        return cached
    filename = getattr(module, "__file__", None)
    if not filename:
        raise ValueError(f"module {module.__name__} has no source file")
    source = Path(filename).read_text(encoding="utf-8")
    definitions: _WholeTree | _ChunkedTree
    if filename.endswith(".hy"):
        definitions = _compile_hy(source, filename, module.__name__)
    else:
        definitions = _WholeTree(ast.parse(source, filename=filename))
    loaded = _ModuleSource(module=module, definitions=definitions, filename=filename)
    _MODULE_CACHE[module.__name__] = loaded
    return loaded


def _compile_hy(source: str, filename: str, module_name: str) -> "_WholeTree | _ChunkedTree":
    """The macro-expanded tree of a Hy module (from the disk cache when the same inputs were expanded before)."""
    place = _hy_cache_place(source, module_name)
    if place is not None:
        cached = _find_cached_tree(place)
        if cached is not None:
            return cached
    # The whole miss is observed: the expansion, and building and writing what is cached
    # with it (the derived values of _CachedTree — agora-redesign #1590). All of it is paid
    # once per changed source, so none of it is a test's weight.
    with contextlib.ExitStack() as observed:
        for observer in tuple(_EXPANSION_OBSERVERS):
            observed.enter_context(observer())
        expanded = _expand_hy(source, filename, module_name)
        if place is not None:
            _write_cached_tree(place, expanded)
    return _WholeTree(expanded.tree)


# Who wants to know when an expansion missed the cache (the expansion and writing the
# cache entry). A test-time budget subtracts that time the way it subtracts bytecode
# compilation (a cold cache is not the test's weight); the analyzer itself does not know
# who observes.
ExpansionObserver = Callable[[], contextlib.AbstractContextManager[None]]
_EXPANSION_OBSERVERS: list[ExpansionObserver] = []


def observe_expansions(observer: ExpansionObserver) -> Callable[[], None]:
    """Run every uncached Hy expansion inside ``observer()``; returns the function that stops it."""
    _EXPANSION_OBSERVERS.append(observer)

    def stop() -> None:
        """Remove this observer (idempotent)."""
        if observer in _EXPANSION_OBSERVERS:
            _EXPANSION_OBSERVERS.remove(observer)

    return stop


@dataclass(frozen=True)
class _Expanded:
    """A Hy module expanded in this process, and the record of what the expansion used: the Hy
    version and every macro file it went through — the macro modules the source requires, the ones
    they require in turn, and the helpers their macros call (``current_record``, the record the
    import side puts on the module's bytecode)."""

    tree: ast.Module
    macros: "MacroRecord"


def _expand_hy(source: str, filename: str, module_name: str) -> _Expanded:
    """Expand a Hy module with Hy's own compiler so user macros become the Python AST the reader walks."""
    import hy
    import hy.compiler
    from doeff_hy_bytecode_guard import current_record

    # A fresh module object: expansion registers macros on it and must not
    # disturb the imported module whose globals resolve names.
    scratch = types.ModuleType(module_name)
    scratch.__file__ = filename
    compiled = hy.compiler.hy_compile(
        hy.read_many(source, filename=filename), scratch, filename=filename, source=source
    )
    if not isinstance(compiled, ast.Module):
        raise TypeError(f"{filename}: Hy compiled to {type(compiled)!r}, expected a module")
    return _Expanded(tree=compiled, macros=current_record(scratch, filename))


# Macro expansion is most of the analysis time: every process re-expands each Hy
# module it reads (0.5–1 s per module). The expanded tree depends only on the
# source's content, the module name, the macro files the expansion goes through, and
# the Hy / Python versions, and what is stored with it on the reader's code, so it is
# cached on disk under a key made of exactly those — not of the file's path, so the
# same source in another worktree reads the same entry (agora-redesign #3598).
# DOEFF_EFFECT_ANALYZER_CACHE names the directory; "off" disables the cache.
#
# Which macro files an expansion goes through is known only after it (a macro module
# requires others; its macros call helpers of its package), so the key has two levels,
# as the import side's shared code store keys the code by the source and checks the
# record it stores with it (doeff_hy_bytecode_guard): ``_TreeKey`` names the place of
# a source, and in it each entry is named by the digests of the macro files it was
# expanded with and starts with their record. An entry is read only when its record is
# this environment's (``record_is_current_here`` — the files found again by module name,
# since the paths in a record are the writing worktree's). Worktrees of different doeff
# versions keep their own entries side by side instead of overwriting one.

# The shape of the key; a new shape names other entries, and the old ones are never read.
# v2: the tree is stored with what is derived from it alone. v3: cut into top-level
# definitions that are built only when followed (_CachedTree — agora-redesign #1591).
# v4: the body facts keep the names a destructuring binds (_Unpacked — #2674).
# v5: the key names the reader's source (_reader_digest), so a change of what is
# derived no longer waits for this tag to be bumped by hand (#2973 changed the shape
# of _BodyFacts.rewraps and kept v4 — the newer reader read v4 entries and raised).
# v6: the key names the source by its content and not by its path (_TreeKey — #3598).
# v7: the macro files are every one the expansion went through, not only the modules the
# source requires itself (_Expanded.macros — #3598).
_TREE_KEY_FORMAT = "v7"


@dataclass(frozen=True)
class _TreeKey:
    """What the place of an expanded tree is a function of. The file's path is not one of them:
    the expansion reads the path only for its suffix (``.hyk`` / ``.hyp`` decide whether ``defp``
    is refused or ``defk`` warned about, never the tree), nothing derived from the tree holds it,
    and the reader takes the path it reports from the module it reads
    (``_ModuleSource.filename``) — so a worktree whose file has the same content reads the entry
    another worktree wrote (agora-redesign #3598: a closure test of the screen job took 120.7 s
    in a new worktree and 5.9 s after). The macro files name the entry within the place
    (``_entry_name``)."""

    python: str
    hy: str
    reader: str
    record_format: str
    module_name: str
    source_sha256: str

    def place(self, directory: Path) -> Path:
        """The directory of this source's entries: the digest of every field, so any change misses."""
        text = "\n".join(
            [
                _TREE_KEY_FORMAT,
                self.python,
                self.hy,
                self.reader,
                self.record_format,
                self.module_name,
                self.source_sha256,
            ]
        )
        return directory / hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry_name(macros: "MacroRecord") -> str:
    """The entry of one set of macro files: the digest of their module names and contents (not of
    their paths — those are the writing worktree's)."""
    text = "\n".join(
        [macros.hy_version, *(f"{used.module}={used.sha256}" for used in macros.dependencies)]
    )
    return f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}.pickle"


def _hy_cache_dir() -> Path | None:
    """Where expanded trees are kept (None = caching turned off)."""
    configured = env_places.tree_cache_setting()
    if configured == "off":
        return None
    if configured:
        return Path(configured)
    return env_places.cache_home() / "doeff-effect-analyzer" / "hy-trees"


def _tree_key(source: str, module_name: str) -> _TreeKey:
    """The key of the place of the trees ``source`` expands to as ``module_name`` (see ``_TreeKey``)."""
    import hy
    from doeff_hy_bytecode_guard.records import RECORD_TAG

    return _TreeKey(
        python=sys.version,
        hy=hy.__version__,
        reader=_reader_digest(),
        record_format=RECORD_TAG,
        module_name=module_name,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )


def _hy_cache_place(source: str, module_name: str) -> Path | None:
    """The directory of the entries for these exact inputs (None = caching turned off); an entry in
    it is read only while the macro files it was expanded with are unchanged."""
    directory = _hy_cache_dir()
    if directory is None:
        return None
    return _tree_key(source, module_name).place(directory)


@functools.cache
def _reader_digest() -> str:
    """The digest of this module's source. What is stored with a tree (the definition index, the
    body nodes and the body facts — ``_derived``) is built by the code here, so an entry written by
    another version of the reader — another shape of a fact — is never read by this one."""
    return f"reader={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}"


@dataclass(frozen=True)
class _Definition:
    """One def / class / lambda of a cached tree: which chunk holds it, its place among the
    chunk's definitions, and what ``_find_function`` compares — so a candidate is chosen
    without building any node."""

    chunk: int
    position: int
    is_class: bool
    lines: tuple[int, ...]
    params: tuple[str, ...]


@dataclass(frozen=True)
class _Chunk:
    """One top-level definition of a cached tree with what is derived from it alone: its
    definitions (itself and the ones nested in it, in index order) and, for every def and
    lambda in it, the body nodes and body facts. Pickled on its own, so its values point at
    its own nodes; nothing in it points into another chunk (a def's facts are its own body's)."""

    definitions: tuple[ast.AST, ...]
    body_nodes: tuple[tuple[FunctionNode, tuple[ast.AST, ...]], ...]
    body_facts: tuple[tuple[FunctionNode, _BodyFacts], ...]


@dataclass(frozen=True)
class _CachedTree:
    """An expanded tree as it is kept on disk: the definition index, then one pickled chunk per
    top-level definition, then the whole tree pickled (only ``module_ast`` builds it).

    agora-redesign #1586 / #1590 stored the derived values (definition index, body nodes and
    facts) with the tree, since rebuilding them in every process was most of an analysis.
    What stayed was building the whole tree from the pickle — 0.41–0.45 s of 1.17 s for one
    closure test — although the reader follows a few definitions of each module (#1591).
    Bytes are copied, not built, so a chunk no one follows costs nothing but its read."""

    index: dict[tuple[str, ...], tuple[_Definition, ...]]
    chunks: tuple[bytes, ...]
    whole: bytes


class _ChunkedTree:
    """A cached tree read one top-level definition at a time: ``find`` chooses the candidate
    from the index (as ``_find_function`` would) and builds only the chunk that holds it."""

    def __init__(self, cached: _CachedTree) -> None:
        self._cached = cached
        self._loaded: dict[int, tuple[ast.AST, ...]] = {}
        self._tree: ast.Module | None = None

    def find(self, function: types.FunctionType) -> FunctionNode | None:
        """The def of ``function`` — the same one ``_find_function`` picks from the whole tree."""
        candidates = [
            definition
            for definition in self._cached.index.get(_qualname_path(function), ())
            if not definition.is_class
        ]
        if not candidates:
            return None
        chosen = min(candidates, key=lambda d: _closeness(d.lines, d.params, function.__code__))
        node = self._definitions_of(chosen.chunk)[chosen.position]
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            raise TypeError(f"cached definition {chosen!r} is a {type(node).__name__}, not a def")
        return node

    @property
    def tree(self) -> ast.Module:
        """The whole tree, built the first time it is asked for (``module_ast``)."""
        if self._tree is None:
            tree = pickle.loads(self._cached.whole)
            if not isinstance(tree, ast.Module):
                raise TypeError(f"cached tree is a {type(tree).__name__}, not a module")
            self._tree = tree
        return self._tree

    def _definitions_of(self, chunk: int) -> tuple[ast.AST, ...]:
        """Build a chunk once, placing its derived values in the per-process memos (keyed by
        its nodes) so the reader finds them as if it had built them."""
        loaded = self._loaded.get(chunk)
        if loaded is not None:
            return loaded
        read = pickle.loads(self._cached.chunks[chunk])
        if not isinstance(read, _Chunk):
            raise TypeError(f"cached chunk {chunk} is a {type(read).__name__}")
        for function, nodes in read.body_nodes:
            _BODY_NODES[id(function)] = (function, nodes)
        for function, facts in read.body_facts:
            _BODY_FACTS[id(function)] = (function, facts)
        self._loaded[chunk] = read.definitions
        return read.definitions


def _find_cached_tree(place: Path) -> _ChunkedTree | None:
    """The tree of the entry in ``place`` whose macro files are this environment's (None when no
    entry is — the caller expands again)."""
    return next(
        (tree for entry in sorted(place.glob("*.pickle")) if (tree := _read_cached_tree(entry)) is not None),
        None,
    )


def _read_cached_tree(path: Path) -> _ChunkedTree | None:
    """The entry's tree when the macro files it was expanded with are this environment's; None when
    they are not, or the entry is absent or unreadable. The entry starts with the record of those
    files, so an entry of other files is not read past it."""
    from doeff_hy_bytecode_guard import record_is_current_here
    from doeff_hy_bytecode_guard.records import MacroRecord

    try:
        with path.open("rb") as handle:
            macros = pickle.load(handle)
            if not isinstance(macros, MacroRecord) or not record_is_current_here(macros):
                return None
            cached = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError):
        return None
    if not isinstance(cached, _CachedTree):
        return None
    return _ChunkedTree(cached)


def _derived(tree: ast.Module) -> _CachedTree:
    """The tree cut into its top-level definitions, each with its body nodes and body facts
    (a def the reader cannot read is left out and read on demand as before), and the index
    that names where each definition is. The index keeps ``_definitions_by_path``'s order,
    so ties in ``find`` resolve as they do on the whole tree."""
    index: dict[tuple[str, ...], list[_Definition]] = {}
    chunks: list[bytes] = []
    for number, root in enumerate(_direct_definitions(tree.body)):
        definitions: list[ast.AST] = []

        def walk(definition: ast.AST, path: tuple[str, ...]) -> None:
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            name = "<lambda>" if isinstance(definition, ast.Lambda) else definition.name
            here = (*path, name)
            index.setdefault(here, []).append(
                _Definition(
                    chunk=number,
                    position=len(definitions),
                    is_class=isinstance(definition, ast.ClassDef),
                    lines=() if isinstance(definition, ast.ClassDef) else _definition_lines(definition),
                    params=() if isinstance(definition, ast.ClassDef) else _definition_params(definition),
                )
            )
            definitions.append(definition)
            members = [definition.body] if isinstance(definition, ast.Lambda) else definition.body
            for inner in _direct_definitions(members):
                walk(inner, here)

        walk(root, ())
        functions = [node for node in ast.walk(root) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]
        facts: list[tuple[FunctionNode, _BodyFacts]] = []
        for function in functions:
            try:
                facts.append((function, _body_facts(function)))
            except Exception:  # noqa: BLE001 — a def the reader cannot read now is read (and fails) when followed, as before
                continue
        chunk = _Chunk(
            definitions=tuple(definitions),
            body_nodes=tuple((function, _body_nodes(function)) for function in functions),
            body_facts=tuple(facts),
        )
        chunks.append(pickle.dumps(chunk, protocol=pickle.HIGHEST_PROTOCOL))
    return _CachedTree(
        index={path: tuple(entries) for path, entries in index.items()},
        chunks=tuple(chunks),
        whole=pickle.dumps(tree, protocol=pickle.HIGHEST_PROTOCOL),
    )


def _write_cached_tree(place: Path, expanded: _Expanded) -> None:
    """Write the entry of the expansion's macro files in ``place`` — their record, then the tree —
    atomically; a cache that cannot be written only costs the next expansion."""
    try:
        cached = _derived(expanded.tree)
        place.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=place, suffix=".tmp")
        with os.fdopen(handle, "wb") as out:
            pickle.dump(expanded.macros, out, protocol=pickle.HIGHEST_PROTOCOL)
            pickle.dump(cached, out, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, place / _entry_name(expanded.macros))
    except (OSError, pickle.PicklingError, RecursionError):
        return


def resolve_target(spec: str) -> Any:
    """``package.module:attr[.attr…]`` → the object (imports the module)."""
    if ":" not in spec:
        raise ValueError(f"target must be 'module:attr', got {spec!r}")
    module_name, attr_path = spec.split(":", 1)
    _ensure_hy_importable()
    obj: Any = importlib.import_module(module_name)
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj


def _ensure_hy_importable() -> None:
    try:
        import hy  # noqa: F401 - registers the .hy import hook
    except ImportError:
        return


# --------------------------------------------------------------------------- classify


def _effect_base() -> type:
    from doeff_vm import EffectBase

    return EffectBase


def _bind_opener_class() -> type:
    """doeff-vm の BindOpener の型 — ``outcomes.open_bind`` の奥の Python の道を読むため(``_function_of``)。"""
    from doeff_vm import BindOpener

    return BindOpener


def _is_effect_class(obj: Any) -> bool:
    return isinstance(obj, type) and issubclass(obj, _effect_base())


def _is_control_class(obj: Any) -> bool:
    return isinstance(obj, type) and obj.__module__.split(".")[0] == "doeff_vm"


def _function_of(obj: Any) -> types.FunctionType | None:
    """The plain function behind a Program function (``@do``/``defk`` wrappers unwrapped)."""
    if isinstance(obj, types.MethodType):
        obj = obj.__func__
    if isinstance(obj, _bind_opener_class()):
        # ``outcomes.open_bind`` is doeff-vm's BindOpener: its in-place path gives the answer
        # its Python path gives, so read the Python path (agora-redesign #844).
        obj = obj.fallback
    if not callable(obj) or isinstance(obj, type):
        return None
    unwrapped = inspect.unwrap(obj)
    if isinstance(unwrapped, types.FunctionType):
        return unwrapped
    return None


def _is_installer(obj: Any) -> bool:
    """A handler value: a Program -> Program installer (``defhandler``, ``doeff.handler(raw)``)."""
    return getattr(obj, "_doeff_is_handler_fn", False) is True


# The doeff functions that install handlers, looked up by identity (``doeff`` re-exports
# them; the package attribute ``doeff.program`` is shadowed by the function ``program``).
def _doeff_program_module() -> types.ModuleType:
    """``doeff.program`` itself (not the shadowing function)."""
    return importlib.import_module("doeff.program")


def _with_handlers_function() -> Any:
    """``doeff.with_handlers`` — installs a handler list around a Program."""
    return _doeff_program_module().with_handlers


def _handler_wrapper_function() -> Any:
    """``doeff.handler`` — turns a raw ``(effect, k)`` dispatcher into an installer."""
    return _doeff_program_module().handler


def _with_handler_node() -> Any:
    """``doeff_vm.WithHandler`` — the VM node every install ends in (``handle`` writes it)."""
    from doeff_vm import WithHandler

    return WithHandler


def _do_decorator() -> Any:
    """``doeff.do`` — wraps a dispatcher; read through to the function it wraps."""
    return importlib.import_module("doeff.do").do


# What a name in the code under analysis resolves to: any object its modules define
# or import (a class, a function, a list of handler values …).  The reader's one
# boundary with arbitrary user objects; each use checks the kind it needs
# (_is_effect_class / _function_of / _is_installer) before relying on it.
Imported = Any

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


class _Unbound:
    """What a name resolves to when the reader cannot bind it to an object."""

    def __repr__(self) -> str:
        return "UNBOUND"


UNBOUND: Any = _Unbound()


@dataclass(frozen=True)
class _Instance:
    """``SomeClass(...)`` bound to a name (or ``self`` in a method): attributes resolve to
    the class's methods, and to what ``__init__`` assigns to ``self``."""

    cls: type


@dataclass(frozen=True)
class Binding:
    """A parameter bound to what the caller passed."""

    name: str
    value: Imported


@dataclass(frozen=True)
class ReceivedEffect:
    """A handler clause's effect parameter read inside the clause for ``cls``: the effect
    the clause received.  Performing it (``yield effect`` — a meter observing and passing
    the same effect outward) is a forwarded use of ``cls``: it leaves as the class that
    arrived (``pass_through``), answered further out."""

    cls: type


@dataclass(frozen=True)
class ReceivedField:
    """``effect.<attr>`` for a clause's received effect (``ReceivedEffect``): a field of
    the effect that arrived.  Performing it runs a Program the effect carried
    (``Try(program)`` → ``yield effect.program``); that Program was read where the
    effect was performed — carried with the effect class as carrier — so performing it
    here adds nothing (agora-redesign #1432)."""

    cls: type
    attr: str


class _IdentityKind(Enum):
    PROGRAM = "program"  # a Program argument: its function and bindings
    PROGRAM_SEQUENCE = "program-sequence"  # a tuple / list of Program arguments
    PROGRAM_ELEMENT = "program-element"  # one element of such a sequence, not known which
    LOCAL_FUNCTION = "local-function"  # a lambda / nested def, read where it was written
    WRITTEN_ARGUMENT = "written-argument"  # a builder argument, read as a handler where written
    RECEIVED_FIELD = "received-field"  # a field of a clause's received effect
    INSTANCE = "instance"  # an instance of a class
    OBJECT = "object"  # any other object, by id


@dataclass(frozen=True)
class _Identity:
    """A hashable identity for a bound value (bound objects need not be hashable)."""

    kind: _IdentityKind
    ident: int
    bindings: "_BindingsKey | None" = None


@dataclass(frozen=True)
class _KeyEntry:
    """One parameter of a ``_BindingsKey``."""

    name: str
    identity: _Identity


@dataclass(frozen=True)
class _BindingsKey:
    """What a function was read with — a function read with the same key is read once."""

    entries: frozenset[_KeyEntry] = frozenset()


@dataclass(frozen=True)
class _Bound:
    """The parameters of one function read with known arguments."""

    bindings: tuple[Binding, ...] = ()

    def get(self, name: str) -> Imported:
        """What ``name`` is bound to, or ``UNBOUND``."""
        for binding in self.bindings:
            if binding.name == name:
                return binding.value
        return UNBOUND

    def names(self) -> frozenset[str]:
        """The bound parameter names."""
        return frozenset(binding.name for binding in self.bindings)

    def without(self, names: frozenset[str]) -> "_Bound":
        """Drop names the body rebinds (they no longer hold what the caller passed)."""
        return _Bound(tuple(b for b in self.bindings if b.name not in names))

    def plus(self, other: "_Bound") -> "_Bound":
        """``other`` wins for a name bound in both."""
        mine = tuple(b for b in self.bindings if b.name not in other.names())
        return _Bound(mine + other.bindings)

    @functools.cached_property
    def key(self) -> _BindingsKey:
        """Cache key: a function read with the same bindings is read once.

        Built once per ``_Bound`` (the bindings are frozen, and a written value's identity
        names its scope's key — so rebuilding it walked every enclosing scope again on each
        read; agora-redesign #1592)."""
        return _BindingsKey(frozenset(_KeyEntry(b.name, _identity(b.value)) for b in self.bindings))

    @property
    def depth(self) -> int:
        """How deeply Program arguments nest (bounded, so recursion through arguments ends)."""
        return max(
            (b.value.depth for b in self.bindings if isinstance(b.value, _PROGRAM_VALUES)),
            default=0,
        )


_NO_BINDINGS = _Bound()
_MAX_BINDING_DEPTH = 4


@dataclass(frozen=True, eq=False)
class _ProgramArg:
    """A parameter bound to a Program the caller built (``f(business())``,
    ``f(with_handlers([...], body))``): performing the parameter performs that
    expression where the caller wrote it (``scope``)."""

    expr: ast.expr
    scope: "_Scope"

    @property
    def filename(self) -> str:
        """The file the expression was written in."""
        return _module_source(self.scope.module).filename

    @property
    def depth(self) -> int:
        """1 + the nesting of the Program arguments its scope was itself bound with."""
        return 1 + self.scope.bound.depth


@dataclass(frozen=True, eq=False)
class _ProgramSeq:
    """A parameter bound to a tuple / list of Programs the caller built
    (``f(parts, #((serve-a) (serve-b)))`` — one body per port).  It is not a Program
    itself: what the callee takes out of it (``for p in ps`` / ``ps[i]``) is one of the
    elements, read where the caller wrote it (agora-redesign #1265)."""

    expr: ast.Tuple | ast.List
    scope: "_Scope"

    @property
    def elements(self) -> tuple[_ProgramArg, ...]:
        """Each element, as a Program argument written in the caller's scope."""
        return tuple(_ProgramArg(element, self.scope) for element in self.expr.elts)

    @property
    def depth(self) -> int:
        """The depth its elements have (they are written in the same scope)."""
        return 1 + self.scope.bound.depth


@dataclass(frozen=True, eq=False)
class _ProgramAnyOf:
    """An element taken out of a ``_ProgramSeq`` where the reader cannot tell which (a
    loop variable, an index it cannot read): performing it performs each element, as
    performing a conditional performs both branches."""

    sequence: _ProgramSeq

    @property
    def depth(self) -> int:
        """The sequence's depth."""
        return self.sequence.depth


@dataclass(frozen=True, eq=False)
class _LocalFunction:
    """A lambda or a def nested in a body (``(fn [] (begin-query c))`` passed to a
    callee, ``attempt`` defined in a handler clause): calling it runs its body, read
    where it was written (``scope``) with its parameters bound to the call's arguments
    (agora-redesign #1432)."""

    node: FunctionNode
    scope: "_Scope"

    @property
    def filename(self) -> str:
        """The file the function was written in."""
        return _module_source(self.scope.module).filename

    @property
    def depth(self) -> int:
        """1 + the nesting of the Program arguments its scope was itself bound with."""
        return 1 + self.scope.bound.depth


@dataclass(frozen=True, eq=False)
class _WrittenArgument:
    """A handler-list builder's parameter bound to the expression the caller wrote for it
    when the reader cannot bind it to an object (``(managed-launch [] adapter)`` — a list
    literal, a name the caller unpacked).  Where the builder places the parameter in its list
    (``[mark #* around adapter]``), the expression is read as a handler (list) in the
    caller's ``scope`` (agora-redesign #2674)."""

    expr: ast.expr
    scope: "_Scope"

    @property
    def filename(self) -> str:
        """The file the expression was written in."""
        return _module_source(self.scope.module).filename


@dataclass(frozen=True)
class _Unpacked:
    """A local bound by exactly one destructuring assignment (``[lower, adapter] = f()``,
    Hy's ``(setv [lower adapter] (f))``) and nothing else: the element at ``index`` of what
    ``value`` holds, unpacked into ``count`` names."""

    name: str
    value: ast.expr
    index: int
    count: int


_PROGRAM_VALUES = (_ProgramArg, _ProgramSeq, _ProgramAnyOf, _LocalFunction)


def _programs_of(value: "_ProgramArg | _ProgramAnyOf") -> tuple[_ProgramArg, ...]:
    """The Programs performing ``value`` may run: itself, or each element it may be."""
    match value:
        case _ProgramAnyOf(sequence=sequence):
            return sequence.elements
        case _ProgramArg():
            return (value,)


def _passed_programs(value: Imported) -> tuple["_ProgramArg | _ProgramAnyOf", ...]:
    """The Programs a bound value hands the callee: a Program argument, an element taken
    out of a sequence (and each element it may be), or each element of a sequence."""
    match value:
        case _ProgramSeq(elements=elements):
            return elements
        case _ProgramAnyOf(sequence=sequence):
            return (value, *sequence.elements)
        case _ProgramArg():
            return (value,)
        case _:
            return ()


def _label_of(program: "_ProgramArg | _ProgramAnyOf") -> str:
    """How a carried Program is named in reports: the expression it was written as, or
    the sequence an element was taken out of."""
    match program:
        case _ProgramAnyOf(sequence=sequence):
            return f"an element of {ast.unparse(sequence.expr)}"
        case _ProgramArg(expr=expr):
            return ast.unparse(expr)


def _element_of(sequence: Imported, index: ast.expr) -> Imported:
    """``sequence[index]`` for a sequence of Programs: the element at a constant index,
    or any element when the index is not a constant the reader can read."""
    if not isinstance(sequence, _ProgramSeq):
        return UNBOUND
    elements = sequence.elements
    match index:
        case ast.Constant(value=int() as position) if -len(elements) <= position < len(elements):
            return elements[position]
        case ast.Constant():
            return UNBOUND  # a constant outside the sequence (or not an index) names no element
        case _:
            return _ProgramAnyOf(sequence)


@dataclass(frozen=True)
class _ReadKey:
    """One function read with one set of bindings — the unit the reader caches and
    the unit a recursion check compares."""

    function: types.FunctionType
    bindings: _BindingsKey


def _identity(value: Imported) -> _Identity:
    """A hashable identity for a bound value (bound objects need not be hashable)."""
    written = _written_identity(value)
    if written is not None:
        return written
    match value:
        case ReceivedField():
            return _Identity(_IdentityKind.RECEIVED_FIELD, hash(value))
        case _Instance(cls=cls):
            return _Identity(_IdentityKind.INSTANCE, id(cls))
        case _:
            return _Identity(_IdentityKind.OBJECT, id(value))


def _written_identity(value: Imported) -> _Identity | None:
    """The identity of a value written in a caller's body (a Program, a sequence of them,
    an element of one, a local function): where it was written and the scope's bindings."""
    match value:
        case _ProgramArg(expr=expr, scope=scope):
            return _Identity(_IdentityKind.PROGRAM, id(expr), scope.bound.key)
        case _ProgramSeq(expr=expr, scope=scope):
            return _Identity(_IdentityKind.PROGRAM_SEQUENCE, id(expr), scope.bound.key)
        case _ProgramAnyOf(sequence=sequence):
            return _Identity(
                _IdentityKind.PROGRAM_ELEMENT, id(sequence.expr), sequence.scope.bound.key
            )
        case _LocalFunction(node=node, scope=scope):
            return _Identity(_IdentityKind.LOCAL_FUNCTION, id(node), scope.bound.key)
        case _WrittenArgument(expr=expr, scope=scope):
            return _Identity(_IdentityKind.WRITTEN_ARGUMENT, id(expr), scope.bound.key)
        case _:
            return None


@dataclass(frozen=True)
class _Resolution:
    """What one expression resolved to in one scope (``_Scope.resolve``'s memo entry).
    ``value`` is whatever the expression denotes — any object, as ``resolve`` answers."""

    expr: ast.expr
    value: Any  # the denoted object: a module global, class, function or reader value


@dataclass(frozen=True)
class _Scope:
    """Name resolution inside one function body (and the functions it is nested in)."""

    module: types.ModuleType
    local_names: frozenset[str]
    local_calls: dict[str, ast.Call]
    local_imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    # A local bound exactly once → the expression bound (``x = yield f()`` → the Yield).
    local_values: dict[str, ast.expr] = field(default_factory=dict)
    local_functions: dict[str, FunctionNode] = field(default_factory=dict)
    # A local bound by exactly one ``for`` (and nothing else) → what the loop walks.
    local_elements: dict[str, ast.expr] = field(default_factory=dict)
    # A local assigned on several paths, none reading it (Hy's ``match`` / ``cond`` as an
    # expression: ``_hy_anon_1 = …`` once per branch) → every value it may hold.
    local_choices: dict[str, tuple[ast.expr, ...]] = field(default_factory=dict)
    # A local first bound on each path it is bound on and then only rebound to a call that
    # wraps it (``prog = h(prog)`` — handlers put back around a Program) → those first values.
    local_rewraps: dict[str, tuple[ast.expr, ...]] = field(default_factory=dict)
    # A local bound by one destructuring assignment and nothing else → where it was unpacked from.
    local_unpacked: dict[str, _Unpacked] = field(default_factory=dict)
    bound: _Bound = _NO_BINDINGS
    # What each expression resolved to in this scope, by id(expr).  Resolution
    # reads only the frozen fields above and the module's globals, so a scope answers the
    # same expression the same way; the reader asks about 4 times per expression (#1592).
    # ``init=False`` so ``dataclasses.replace`` (another ``bound``) starts empty; the expr is
    # kept beside the value so a reused id never answers for another node.
    _resolved: "dict[int, _Resolution]" = field(
        default_factory=dict, init=False, compare=False, repr=False
    )

    def resolve(self, expr: ast.expr) -> Any:
        """The object a Name / Attribute chain denotes (rooted at a module global, at a
        name the function imports itself, or at a bound parameter), or ``UNBOUND``."""
        known = self._resolved.get(id(expr))
        if known is not None and known.expr is expr:
            return known.value
        value = self._resolve_expr(expr)
        self._resolved[id(expr)] = _Resolution(expr, value)
        return value

    def _resolve_expr(self, expr: ast.expr) -> Any:
        """``resolve`` without the memo (the reading itself)."""
        if isinstance(expr, ast.Name):
            return self._resolve_name(expr.id)
        if isinstance(expr, ast.Attribute):
            base = self.resolve(expr.value)
            if isinstance(base, ReceivedEffect):
                return ReceivedField(base.cls, expr.attr)
            if isinstance(base, _Instance):
                return _instance_attribute(base, expr.attr)
            if base is not UNBOUND and hasattr(base, expr.attr):
                return getattr(base, expr.attr)
        if isinstance(expr, ast.Call):
            return self._imported_module(expr)
        if isinstance(expr, ast.NamedExpr):
            # ``(m := __import__(...)).open_bind`` — doeff-hy's bind names what it reaches.
            return self.resolve(expr.value)
        if isinstance(expr, ast.Subscript):
            # ``ports[0]`` — an element of a sequence of Programs the caller passed.
            return _element_of(self.resolve(expr.value), expr.slice)
        return UNBOUND

    def _imported_module(self, call: ast.Call) -> Any:
        """``__import__("pkg.mod", fromlist=...)`` → the module ``pkg.mod`` (the form
        doeff-hy's expansions use to reach a helper without an import statement).
        Without ``fromlist`` ``__import__`` answers the top package, so only that form is read."""
        if self.resolve(call.func) is not __import__ or not call.args:
            return UNBOUND
        name = call.args[0]
        has_fromlist = any(item.arg == "fromlist" for item in call.keywords)
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)) or not has_fromlist:
            return UNBOUND
        return importlib.import_module(name.value)

    def _resolve_name(self, name: str) -> Any:
        if name in self.local_imports:
            return _import_binding(*self.local_imports[name])
        bound = self.bound.get(name)
        if bound is not UNBOUND:
            return bound
        local = self._local_value(name)
        if local is not UNBOUND:
            return local
        if name in self.local_names:
            return self._local_instance(name)
        for table in (vars(self.module), _builtins_of(self.module)):
            if name in table:
                return table[name]
        return UNBOUND

    def _local_element(self, name: str) -> Imported:
        """An element of a sequence of Programs a local holds: the variable of a loop
        over one (any element), or a local bound once to ``ports[i]``."""
        walked = self.local_elements.get(name)
        if walked is not None:
            sequence = self.resolve(walked)
            return _ProgramAnyOf(sequence) if isinstance(sequence, _ProgramSeq) else UNBOUND
        value = self.local_values.get(name)
        if isinstance(value, ast.Subscript) and not _reads_itself(value, name):
            element = self.resolve(value)
            return element if isinstance(element, (_ProgramArg, _ProgramAnyOf)) else UNBOUND
        return UNBOUND

    def _local_value(self, name: str) -> Imported:
        """What a local the reader follows holds: an element of a sequence of Programs, a
        field of the received effect, or a function defined in the body — else ``UNBOUND``."""
        if name in self.local_functions:
            return _LocalFunction(self.local_functions[name], self)
        element = self._local_element(name)
        return element if element is not UNBOUND else self._received_field(name)

    def _received_field(self, name: str) -> Imported:
        """A local holding a field of the received effect: bound once to
        ``effect.<attr>`` (``program = effect.program`` — what ``defhandler`` expands a
        clause's field pattern to), or first bound to it and then only wrapped in
        handlers (``prog = effect.program`` … ``prog = h(prog)`` — ``try_handler``
        puts the inner handlers back around the Program it received).  A first value may
        also be the Program a declared function field builds (``prog = effect.f(item)`` —
        doeff-traverse's ``sequential``, agora-redesign #2973), and a local first bound on
        several paths holds a field only when every first value does."""
        if name in self.local_values:
            seeds: tuple[ast.expr, ...] = (self.local_values[name],)
        else:
            seeds = self.local_rewraps.get(name, ())
        fields = [self._seed_field(seed, name) for seed in seeds]
        if not fields or any(found is UNBOUND for found in fields):
            return UNBOUND
        return fields[0]

    def _seed_field(self, seed: ast.expr, name: str) -> Imported:
        """``effect.<attr>`` → that field; ``effect.<attr>(…)`` for a function field the
        effect declares in ``__doeff_runs_carried__`` → that field (the Program it builds is
        read where the effect was performed); anything else → ``UNBOUND``."""
        if _reads_itself(seed, name):
            return UNBOUND
        match seed:
            case ast.Attribute():
                found = self.resolve(seed)
                return found if isinstance(found, ReceivedField) else UNBOUND
            case ast.Call(func=func):
                built = self.resolve(func)
                return built if _builds_carried(built) else UNBOUND
            case _:
                return UNBOUND

    def _local_instance(self, name: str) -> Any:
        """``runtime = SomeClass(...)`` → an instance of ``SomeClass`` (for its methods)."""
        value = self.local_values.get(name)
        if not isinstance(value, ast.Call):
            return UNBOUND
        if _reads_itself(value, name):
            return UNBOUND
        cls = self.resolve(value.func)
        if (
            isinstance(cls, type)
            and cls.__module__ != "builtins"
            and not _is_effect_class(cls)
            and not _is_control_class(cls)
        ):
            return _Instance(cls)
        return UNBOUND


def _reads_itself(value: ast.expr, name: str) -> bool:
    """Whether the call (or subscript) bound to ``name`` reads ``name`` anywhere — its
    callee, its positional arguments or its keywords (``x = x.method()``,
    ``state = replace(state, ...)``, ``ports = ports[1]``).  There the name inside is the
    earlier value, not the bound one: a local bound that way is neither followed through
    the value nor read as an instance of it."""
    return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(value))


def _builtins_of(module: types.ModuleType) -> dict[str, Any]:
    builtins = vars(module).get("__builtins__")
    if isinstance(builtins, dict):
        return builtins
    if isinstance(builtins, types.ModuleType):
        return vars(builtins)
    return {}


_MISSING = object()


def _instance_attribute(instance: _Instance, attr: str) -> Imported:
    """A method (or class attribute) of the instance's class, or what ``__init__``
    assigns to ``self.<attr>`` (``self._handler = self.handle``)."""
    static = inspect.getattr_static(instance.cls, attr, _MISSING)
    if isinstance(static, (staticmethod, classmethod)):
        return static.__func__
    if static is not _MISSING:
        return static
    return _assigned_in_init(instance, attr)


def _assigned_in_init(instance: _Instance, attr: str) -> Imported:
    """What ``__init__`` assigns to ``self.<attr>``, read with ``self`` bound to the instance."""
    init = inspect.getattr_static(instance.cls, "__init__", _MISSING)
    init_function = _function_of(init) if init is not _MISSING else None
    located = _locate(init_function, _NO_BINDINGS) if init_function is not None else None
    if located is None or isinstance(located, Unresolved) or not _positional_params(located.node):
        return UNBOUND
    self_name = _positional_params(located.node)[0]
    scope = _scope_of(
        located.node, located.scope.module, bound=_Bound((Binding(self_name, instance),))
    )
    for child in _body_nodes(located.node):
        match _single_assignment(child):
            case _Assignment(
                target=ast.Attribute(value=ast.Name(id=owner), attr=name), value=value
            ) if owner == self_name and name == attr:
                return scope.resolve(value)
            case _:
                continue
    return UNBOUND


@dataclass(frozen=True)
class _Assignment:
    target: ast.expr
    value: ast.expr


def _single_assignment(node: ast.AST) -> _Assignment | None:
    """``x = v`` / ``x: T = v`` / ``(x := v)`` with one target (what a name or attribute is
    bound to)."""
    match node:
        case ast.Assign(targets=[target], value=value):
            return _Assignment(target, value)
        case ast.NamedExpr(target=target, value=value):
            return _Assignment(target, value)
        case ast.AnnAssign(target=target, value=ast.expr() as value):
            return _Assignment(target, value)
        case _:
            return None


@dataclass(frozen=True)
class _Call:
    """A Program function called (performed) from a body, with the arguments it was given."""

    function: types.FunctionType
    bound: _Bound
    location: Location


@dataclass(frozen=True)
class _Carried:
    """A Program handed to ``carrier`` (its facts, read where it was written)."""

    carrier: Any
    body: "_Facts"
    label: str
    location: Location


@dataclass(frozen=True)
class _HandledFacts:
    """A Program run under handlers installed in the body (read before following calls)."""

    handlers: tuple[HandlerEffects, ...]
    body: "_Facts"
    text: str
    location: Location


@dataclass
class _Facts:
    """What one function body does, before following calls."""

    # (effect class, where, forwarded — the received effect passed on, see ``EffectUse``)
    effects: list[tuple[type, Location, bool]] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)
    carried: list[_Carried] = field(default_factory=list)
    unresolved: list[tuple[str, str, Location]] = field(default_factory=list)
    handled: list[_HandledFacts] = field(default_factory=list)
    # Program arguments (from the caller) this body performs — a callee that runs the
    # Program it is given is read as running it, not as carrying it.
    performed_arguments: set[_Identity] = field(default_factory=set)


_BODY_NODES: dict[int, tuple[ast.AST, tuple[ast.AST, ...]]] = {}


def _body_nodes(function: FunctionNode) -> tuple[ast.AST, ...]:
    """Nodes of the function body, not descending into nested functions/classes/lambdas.

    Listed once per def node: the reader and the handler reader walk the same bodies many
    times over (every call followed, every clause read), and the tree never changes.
    """
    cached = _BODY_NODES.get(id(function))
    if cached is not None and cached[0] is function:
        return cached[1]
    nodes = tuple(_walk_body_nodes(function))
    _BODY_NODES[id(function)] = (function, nodes)
    return nodes


def _walk_body_nodes(function: FunctionNode) -> Iterator[ast.AST]:
    """Walk the body depth-first in source order (the order ``_body_nodes`` keeps)."""
    body: list[ast.AST] = (
        [function.body] if isinstance(function, ast.Lambda) else list(function.body)
    )
    stack: list[ast.AST] = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _positional_params(function: FunctionNode) -> list[str]:
    """Positional parameter names, in order (``self`` first for a method)."""
    return [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]


def _scope_of(
    function: FunctionNode,
    module: types.ModuleType,
    *,
    parent: _Scope | None = None,
    bound: _Bound = _NO_BINDINGS,
) -> _Scope:
    """The names ``function`` binds, on top of ``parent`` (the function it is nested in)."""
    facts = _body_facts(function)
    names = set(facts.names)
    imports = facts.imports
    values = facts.values
    local_calls = facts.local_calls
    local_functions = facts.local_functions
    elements = facts.elements
    choices = facts.choices
    rewraps = facts.rewraps
    unpacked = facts.unpacked
    # A parameter rebound in the body (by an assignment or a loop) no longer holds what
    # the caller passed.
    own_bound = bound.without(facts.rebound)
    if parent is None:
        return _Scope(
            module=module,
            local_names=frozenset(names - set(imports)),
            local_calls=local_calls,
            local_imports=imports,
            local_values=values,
            local_functions=local_functions,
            local_elements=elements,
            local_choices=choices,
            local_rewraps=rewraps,
            local_unpacked=unpacked,
            bound=own_bound,
        )
    shadowed = frozenset(names)
    return _Scope(
        module=module,
        local_names=frozenset((names | parent.local_names) - set(imports)),
        local_calls={**_unshadowed(parent.local_calls, shadowed), **local_calls},
        local_imports={**parent.local_imports, **imports},
        local_values={**_unshadowed(parent.local_values, shadowed), **values},
        local_functions={**_unshadowed(parent.local_functions, shadowed), **local_functions},
        local_elements={**_unshadowed(parent.local_elements, shadowed), **elements},
        local_choices={**_unshadowed(parent.local_choices, shadowed), **choices},
        local_rewraps={**_unshadowed(parent.local_rewraps, shadowed), **rewraps},
        local_unpacked={**_unshadowed(parent.local_unpacked, shadowed), **unpacked},
        bound=parent.bound.without(shadowed).plus(own_bound),
    )


@dataclass(frozen=True)
class _BodyFacts:
    """What one function body binds — independent of the caller's bindings and the enclosing
    scope, so it is read once per def (``_scope_of`` runs for every call the reader follows).
    The mappings are shared between scopes and never mutated."""

    names: frozenset[str]
    imports: dict[str, tuple[str, str | None]]
    values: dict[str, ast.expr]
    local_calls: dict[str, ast.Call]
    local_functions: dict[str, FunctionNode]
    elements: dict[str, ast.expr]
    choices: dict[str, tuple[ast.expr, ...]]
    rewraps: dict[str, tuple[ast.expr, ...]]
    unpacked: dict[str, _Unpacked]
    rebound: frozenset[str]


_BODY_FACTS: dict[int, tuple[FunctionNode, _BodyFacts]] = {}


def _body_facts(function: FunctionNode) -> _BodyFacts:
    """The bindings of ``function``'s body, read once per def node (see ``_BodyFacts``)."""
    cached = _BODY_FACTS.get(id(function))
    if cached is not None and cached[0] is function:
        return cached[1]
    facts = _read_body_facts(function)
    _BODY_FACTS[id(function)] = (function, facts)
    return facts


def _read_body_facts(function: FunctionNode) -> _BodyFacts:
    """Walk the body once and collect what it binds (parameters, assignments, loops, defs)."""
    arguments = function.args
    names = {arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)}
    if arguments.vararg:
        names.add(arguments.vararg.arg)
    if arguments.kwarg:
        names.add(arguments.kwarg.arg)
    assigned: dict[str, list[ast.expr]] = {}
    imports: dict[str, tuple[str, str | None]] = {}
    functions: dict[str, list[FunctionNode]] = {}
    loops: dict[str, list[ast.expr]] = {}
    for node in _body_nodes(function):
        names |= _bound_names(node)
        imports.update(_import_bindings(node))
        match _single_assignment(node):
            case _Assignment(target=ast.Name(id=name), value=value):
                assigned.setdefault(name, []).append(value)
            case _:
                pass
        match node:
            case ast.For(target=ast.Name(id=name), iter=walked) | ast.AsyncFor(
                target=ast.Name(id=name), iter=walked
            ):
                loops.setdefault(name, []).append(walked)
            case _:
                pass
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
    # A local bound exactly once can be followed through what it was bound to.
    values = {name: exprs[0] for name, exprs in assigned.items() if len(exprs) == 1}
    # A call that reads the name it is bound to is not followed (``_reads_itself`` —
    # following it would read the call as its own argument without end).
    local_calls = {
        name: value
        for name, value in values.items()
        if isinstance(value, ast.Call) and not _reads_itself(value, name)
    }
    local_functions = {name: nodes[0] for name, nodes in functions.items() if len(nodes) == 1}
    # A loop variable bound by one loop and nothing else holds an element of what it walks.
    elements = {
        name: walked[0]
        for name, walked in loops.items()
        if len(walked) == 1 and name not in assigned
    }
    choices = {
        name: tuple(exprs)
        for name, exprs in assigned.items()
        if len(exprs) > 1 and name not in loops and not any(_reads_itself(e, name) for e in exprs)
    }
    rewraps = {
        name: seeds
        for name, exprs in assigned.items()
        if len(exprs) > 1 and (seeds := _rewrapped_seeds(exprs, name))
    }
    places = [place for node in _body_nodes(function) for place in _unpacked_places(node)]
    times = collections.Counter(place.name for place in places)
    # A name unpacked once and bound nowhere else holds that element.
    unpacked = {
        place.name: place
        for place in places
        if times[place.name] == 1 and place.name not in assigned and place.name not in loops
    }
    return _BodyFacts(
        names=frozenset(names),
        imports=imports,
        values=values,
        local_calls=local_calls,
        local_functions=local_functions,
        elements=elements,
        choices=choices,
        rewraps=rewraps,
        unpacked=unpacked,
        rebound=frozenset(assigned) | frozenset(loops) | frozenset(times),
    )


def _unpacked_places(node: ast.AST) -> tuple[_Unpacked, ...]:
    """The names ``[a, b] = v`` / ``a, b = v`` binds, each with its place (only when every
    target is a plain name — a starred or nested target is not followed)."""
    match node:
        case ast.Assign(
            targets=[ast.List(elts=targets) | ast.Tuple(elts=targets)], value=value
        ) if all(isinstance(target, ast.Name) for target in targets):
            return tuple(
                _Unpacked(target.id, value, index, len(targets))
                for index, target in enumerate(targets)
                if isinstance(target, ast.Name)
            )
        case _:
            return ()


def _rewrapped_seeds(exprs: Sequence[ast.expr], name: str) -> tuple[ast.expr, ...]:
    """The first values of a local every other assignment of which wraps it
    (``prog = effect.program`` · ``prog = h(prog)`` · ``prog = try_handler(prog)``), or
    ().  A wrapping call takes the local as its one argument and reads it nowhere else
    (not in its callee).  A local first bound on several paths (one per branch — the
    Traverse and Reduce clauses of doeff-traverse's ``sequential``) has a first value for
    each."""
    seeds = tuple(expr for expr in exprs if not _reads_itself(expr, name))
    wraps = [expr for expr in exprs if _reads_itself(expr, name)]
    if not seeds or not wraps or not all(_wraps(expr, name) for expr in wraps):
        return ()
    return seeds


def _wraps(expr: ast.expr, name: str) -> bool:
    """``f(name)`` / ``(f x)(name)`` with ``name`` read only as the one argument, or
    ``f(g(name))`` — wraps put around a wrap (``prog = try_handler(install_self(prog))``)."""
    match expr:
        case ast.Call(args=[ast.Name(id=argument)], keywords=[], func=func):
            return argument == name and not _reads_itself(func, name)
        case ast.Call(args=[ast.Call() as inner], keywords=[], func=func):
            return _wraps(inner, name) and not _reads_itself(func, name)
        case _:
            return False


def _builds_carried(value: Imported) -> bool:
    """A field of the received effect holding a function its handler calls to build the
    Program it runs where the effect was performed — declared in ``__doeff_runs_carried__``
    (doeff-traverse's ``Traverse.f``).  The Program a call of it builds is read at the
    performing site (``_Reader._carried_builder``), so performing it in the handler adds
    nothing (agora-redesign #2973)."""
    if not isinstance(value, ReceivedField):
        return False
    effects = importlib.import_module("doeff_core_effects.effects")
    return value.attr in effects.runs_carried_of(value.cls)


def _declared_builders(carrier: Imported, call: ast.Call) -> tuple[ast.expr, ...]:
    """The arguments of ``call`` that fill a field ``carrier`` declares in
    ``__doeff_runs_carried__`` — matched to the parameter names of its constructor."""
    if not _is_effect_class(carrier):
        return ()
    effects = importlib.import_module("doeff_core_effects.effects")
    declared = effects.runs_carried_of(carrier)
    if not declared:
        return ()
    names = list(inspect.signature(carrier).parameters)
    leading = itertools.takewhile(lambda a: not isinstance(a, ast.Starred), call.args)
    return (
        *(argument for name, argument in zip(names, leading, strict=False) if name in declared),
        *(keyword.value for keyword in call.keywords if keyword.arg in declared),
    )


def _unshadowed(table: dict[str, Any], shadowed: frozenset[str]) -> dict[str, Any]:
    """The outer function's entries a nested function still sees (not rebound inside)."""
    return {name: value for name, value in table.items() if name not in shadowed}


def _bound_names(node: ast.AST) -> set[str]:
    """Names a body node binds locally (assignment targets, nested defs)."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    return set()


def _import_bindings(node: ast.AST) -> dict[str, tuple[str, str | None]]:
    """``import`` inside a body: local name → (module, attribute or None)."""
    if isinstance(node, ast.Import):
        return {
            (alias.asname or alias.name.split(".")[0]): (
                alias.name if alias.asname else alias.name.split(".")[0],
                None,
            )
            for alias in node.names
        }
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return {(alias.asname or alias.name): (node.module, alias.name) for alias in node.names}
    return {}


def _import_binding(module_name: str, attr: str | None) -> Any:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return UNBOUND
    if attr is None:
        return module
    if hasattr(module, attr):
        return getattr(module, attr)
    try:
        return importlib.import_module(f"{module_name}.{attr}")
    except ImportError:
        return UNBOUND


# --------------------------------------------------------------------------- bindings


def bindings_for(
    function: types.FunctionType,
    *,
    positional: Sequence[Any] = (),
    keywords: Mapping[str, Any] | None = None,
) -> _Bound:
    """Bind ``function``'s parameters to known values (a Hy name is mangled first).

    A name that is not a parameter is refused — a misspelt binding must not be
    ignored silently.
    """
    parameters = inspect.signature(function).parameters.values()
    params = [p.name for p in parameters]
    by_position = [p.name for p in parameters if p.kind in _POSITIONAL_KINDS]
    named = {_mangle(raw_name): raw_name for raw_name in (keywords or {})}
    unknown = [raw_name for name, raw_name in named.items() if name not in params]
    if unknown:
        raise ValueError(
            f"{function.__qualname__} has no parameter {unknown[0]!r} (parameters: {params})"
        )
    return _Bound(
        (
            *(Binding(name, value) for name, value in zip(by_position, positional, strict=False)),
            *(Binding(_mangle(raw_name), value) for raw_name, value in (keywords or {}).items()),
        )
    )


_POSITIONAL_KINDS = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def _mangle(name: str) -> str:
    """A Hy parameter name as Python sees it (``foundation-handlers`` → ``foundation_handlers``)."""
    if name.isidentifier():
        return name
    try:
        import hy
    except ImportError:
        return name
    return hy.mangle(name)


@dataclass(frozen=True)
class _Passed:
    """One parameter of a called function and the argument expression the call gives it."""

    name: str
    argument: ast.expr


def _call_bindings(function: types.FunctionType, call: ast.Call, scope: _Scope) -> _Bound:
    """``function``'s parameters bound to the arguments of ``call`` that are known here."""
    known = (
        Binding(passed.name, _argument_value(passed.argument, scope))
        for passed in _passed_arguments(function, call, scope)
    )
    return _Bound(tuple(b for b in known if b.value is not UNBOUND))


def _passed_arguments(
    function: types.FunctionType, call: ast.Call, scope: _Scope
) -> tuple[_Passed, ...]:
    """The parameters of ``function`` that ``call`` gives an argument (positional up to the
    first ``*spread``, then by keyword)."""
    try:
        params = [
            p
            for p in inspect.signature(function).parameters.values()
            if p.kind is not inspect.Parameter.VAR_KEYWORD
        ]
    except (TypeError, ValueError):
        return ()
    positional = [
        p.name
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    # obj.method(...) through an instance: the instance is the first parameter.
    if isinstance(call.func, ast.Attribute) and isinstance(
        scope.resolve(call.func.value), _Instance
    ):
        positional = positional[1:]
    # Positional arguments up to the first *spread (after it positions are unknown).
    leading = list(itertools.takewhile(lambda a: not isinstance(a, ast.Starred), call.args))
    names = {p.name for p in params}
    return (
        *(_Passed(name, argument) for name, argument in zip(positional, leading, strict=False)),
        *(_Passed(k.arg, k.value) for k in call.keywords if k.arg is not None and k.arg in names),
    )


def _argument_value(argument: ast.expr, scope: _Scope) -> Imported:
    """What an argument denotes when it is known: an object, or a Program built here."""
    value = scope.resolve(argument)
    if isinstance(value, _LocalFunction) or isinstance(argument, ast.Lambda):
        # A function written here, called by the callee (``begin`` / ``commit`` handed to
        # ``run-in-transaction``): read where it was written, bounded like a Program argument.
        local = value if isinstance(value, _LocalFunction) else _LocalFunction(argument, scope)
        return local if local.depth <= _MAX_BINDING_DEPTH else UNBOUND
    if value is not UNBOUND:
        return value
    sequence = _program_sequence(argument, scope)
    if sequence is not None:
        return sequence if sequence.depth <= _MAX_BINDING_DEPTH else UNBOUND
    if not _is_program_expr(argument, scope):
        return value
    program = _ProgramArg(argument, scope)
    return program if program.depth <= _MAX_BINDING_DEPTH else UNBOUND


def _program_sequence(expr: ast.expr, scope: _Scope) -> _ProgramSeq | None:
    """A tuple / list literal whose every element builds a Program (one body per port);
    a literal holding plain data, or spreading another sequence, is not one."""
    match expr:
        case ast.Tuple(elts=[_, *_] as elements) | ast.List(elts=[_, *_] as elements) if all(
            not isinstance(element, ast.Starred) and _is_program_expr(element, scope)
            for element in elements
        ):
            return _ProgramSeq(expr, scope)
        case _:
            return None


def _is_program_expr(expr: ast.expr, scope: _Scope) -> bool:
    """Whether ``expr`` builds a Program: a Program call, an effect, an install form, or
    a name (or an element of a sequence) bound to one (so ``(x)(expr)`` installs a
    handler around it)."""
    match expr:
        case ast.Subscript():
            return isinstance(scope.resolve(expr), (_ProgramArg, _ProgramAnyOf))
        case ast.Name(id=name):
            if isinstance(scope.resolve(expr), (_ProgramArg, _ProgramAnyOf, ReceivedField)):
                return True
            call = scope.local_calls.get(name)
            return call is not None and _is_program_expr(call, scope)
        case ast.Call(func=func):
            if _install_form(expr, scope) is not None:
                return True
            head = scope.resolve(func)
            return head is not UNBOUND and (
                _is_effect_class(head)
                or (not isinstance(head, type) and _function_of(head) is not None)
            )
        case _:
            return False


# --------------------------------------------------------------------------- reading


_MAX_LABEL = 60


class _InstallKind(Enum):
    STACK = "with_handlers"  # with_handlers([h …], body) — a handler list, outermost first
    RAW = "WithHandler"  # WithHandler(dispatch, body) — what `handle` expands to
    VALUE = "installer"  # h(body) / (factory x)(body) — what `with-handler` expands to


@dataclass(frozen=True)
class _InstallForm:
    """A call that runs ``body`` under handlers, before the handlers are read."""

    kind: _InstallKind
    handlers: ast.expr
    body: ast.expr


def _two_argument_form(
    kind: _InstallKind, call: ast.Call, handlers: str, body: str
) -> _InstallForm | None:
    """``with_handlers(handlers, program)`` / ``WithHandler(handler, body)``, by position or keyword."""
    handlers_expr, body_expr = _argument(call, 0, handlers), _argument(call, 1, body)
    if handlers_expr is None or body_expr is None:
        return None
    return _InstallForm(kind, handlers_expr, body_expr)


def _argument(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    """The argument at ``index`` or passed as ``keyword`` (None when absent or spread)."""
    if len(call.args) > index and not isinstance(call.args[index], ast.Starred):
        return call.args[index]
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    return None


@dataclass(frozen=True)
class _BindWrapper:
    """Where a doeff-hy bind wrapper keeps what runs (``operand``) and the failure
    written as ``(<- x e :absent F)`` (``absent`` — None for a wrapper without one)."""

    operand: int
    absent: int | None


@dataclass(frozen=True)
class _Opening:
    """A yielded bind, unwrapped: the ``operand`` that runs and its ``:absent`` failure
    expression (None when the bind has none)."""

    operand: ast.expr
    absent: ast.expr | None


def _bind_wrappers() -> dict[Any, _BindWrapper]:
    """The wrappers doeff-hy's binds put around what runs: ``open_bind(e[, absent])``
    (every ``<-`` / ``!``) and ``direct_bind(token, e)`` (``absent-as`` marks its body and
    the binds written in it)."""
    outcomes = importlib.import_module("doeff_core_effects.outcomes")
    return {
        outcomes.open_bind: _BindWrapper(operand=0, absent=1),
        outcomes.direct_bind: _BindWrapper(operand=1, absent=None),
    }


def _opening(expr: ast.expr, scope: _Scope) -> _Opening | None:
    """``expr`` as doeff-hy's bind wrappers (``open_bind(e)`` / ``open_bind(e, absent)`` /
    ``open_bind(direct_bind(token, e))`` — ADR-DOE-CORE-EFFECTS-003), or None."""
    if not isinstance(expr, ast.Call):
        return None
    wrapper = _bind_wrappers().get(scope.resolve(expr.func))
    operand = None if wrapper is None else _argument(expr, wrapper.operand, "expr")
    if wrapper is None or operand is None:
        return None
    absent = None if wrapper.absent is None else _argument(expr, wrapper.absent, "absent")
    if isinstance(absent, ast.Constant) and absent.value is None:
        absent = None
    inner = _opening(operand, scope)
    if inner is None:
        return _Opening(operand, absent)
    return _Opening(inner.operand, absent if inner.absent is None else inner.absent)


def _bind_expression(expr: ast.expr, scope: _Scope) -> ast.Call | None:
    """The bind wrapper call ``W`` when ``expr`` is doeff-hy's bind expression
    ``b.value if (b := W).__class__ is <Pure> else (yield b)`` (``W`` = ``open_bind(e[, absent])``),
    else None. The expression yields ``W`` unless ``W`` already answered (agora-redesign #844)."""
    match expr:
        case ast.IfExp(
            test=ast.Compare(
                left=ast.Attribute(
                    value=ast.NamedExpr(target=ast.Name(id=bound), value=ast.Call() as wrapper),
                    attr="__class__",
                ),
                ops=[ast.Is()],
            ),
            body=ast.Attribute(value=ast.Name(id=body_name), attr="value"),
            orelse=ast.Yield(value=ast.Name(id=yielded_name)),
        ) if body_name == bound == yielded_name and _opening(wrapper, scope) is not None:
            return wrapper
        case _:
            return None


def _guard_globals_function() -> Any:
    """``doeff_hy.macros._install_guard_globals`` — returns the function it is given."""
    return importlib.import_module("doeff_hy.macros")._install_guard_globals


def _do_block(call: ast.Call, scope: _Scope) -> FunctionNode | None:
    """The body of ``(do! …)`` written as an expression, or None: the expansion
    ``_install_guard_globals(_doeff_do(<local def or lambda>))()`` runs that body there."""
    if call.args or call.keywords or not isinstance(call.func, ast.Call):
        return None
    match _do_wrapped(call.func, scope):
        case ast.Lambda() as node:
            return node
        case ast.Name() as name:
            local = scope.resolve(name)
            return local.node if isinstance(local, _LocalFunction) else None
        case _:
            return None


def _do_wrapped(expr: ast.expr, scope: _Scope) -> ast.expr | None:
    """The function ``_doeff_do(<local def or lambda>)`` wraps — what ``(do! …)`` calls in
    place, and what ``for/do`` hands to ``Traverse`` as its ``f`` — or None."""
    if not isinstance(expr, ast.Call):
        return None
    wrapped = expr
    if (
        len(wrapped.args) == 1
        and isinstance(wrapped.args[0], ast.Call)
        and scope.resolve(wrapped.func) is _guard_globals_function()
    ):
        wrapped = wrapped.args[0]
    if len(wrapped.args) != 1 or scope.resolve(wrapped.func) is not _do_decorator():
        return None
    function = wrapped.args[0]
    return function if isinstance(function, (ast.Lambda, ast.Name)) else None


def _bound_operand(expr: ast.expr, scope: _Scope) -> ast.expr:
    """What a yielded ``expr`` runs: the ``e`` inside doeff-hy's bind wrappers, anything
    else → itself.  open_bind performs ``e`` itself — an undeclared effect or a Program
    passes through unchanged, a declared one is opened after it runs — and direct_bind
    only marks ``e`` for the absent-as around it."""
    opening = _opening(expr, scope)
    return expr if opening is None else opening.operand


def _opened_answers(effect: Imported) -> list[type]:
    """What ``<-`` performs in the binder's scope when it opens ``effect``'s answer:
    Absent for a declared ``:absent`` answer, Raise for a ``:failure`` one (R5・R6)."""
    outcomes = importlib.import_module("doeff_core_effects.outcomes")
    declared = outcomes.outcomes_of(effect)
    if declared is None:
        return []
    effects = importlib.import_module("doeff_core_effects.effects")
    return [
        *([effects.Absent] if declared.absent else []),
        *([effects.Raise] if declared.failure else []),
    ]


@functools.cache
def _absent_receiver() -> HandlerEffects:
    """The receiver ``(<- x e :absent F)`` wraps the one bind in (``absent_raises``:
    Absent → Raise), read from its clauses once."""
    from doeff_effect_analyzer import handler_effects

    outcomes = importlib.import_module("doeff_core_effects.outcomes")
    return handler_effects.analyze_handler(outcomes.absent_raises, name=":absent")


def _install_form(call: ast.Call, scope: _Scope) -> _InstallForm | None:
    """``with_handlers(stack, body)`` / ``WithHandler(h, body)`` / ``h(body)``."""
    head = scope.resolve(call.func)
    if head is not UNBOUND and head is _with_handlers_function():
        form = _two_argument_form(_InstallKind.STACK, call, "handlers", "program")
    elif head is not UNBOUND and head is _with_handler_node():
        form = _two_argument_form(_InstallKind.RAW, call, "handler", "body")
    elif len(call.args) == 1 and not call.keywords and _installs(head, call, scope):
        form = _InstallForm(_InstallKind.VALUE, call.func, call.args[0])
    else:
        form = None
    return form


def _installs(head: Imported, call: ast.Call, scope: _Scope) -> bool:
    """``h(body)`` with ``h`` a handler value, or ``(factory args)(body)`` — what
    ``with-handler [(factory x)] body`` expands to."""
    if head is not UNBOUND:
        return _is_installer(head)
    return isinstance(call.func, ast.Call) and _is_program_expr(call.args[0], scope)


def _read_install(form: _InstallForm, scope: _Scope, filename: str) -> list[HandlerEffects]:
    """The handlers an install form puts around its body, outermost first."""
    # Reading a handler reads its clauses as Programs; the two readings recurse
    # into each other, so the handler side is imported when first needed.
    from doeff_effect_analyzer import handler_effects

    match form.kind:
        case _InstallKind.STACK:
            return handler_effects.stack_of(form.handlers, scope, filename)
        case _InstallKind.RAW:
            text = ast.unparse(form.handlers)
            line = getattr(form.handlers, "lineno", 0)
            label = text if len(text) <= _MAX_LABEL else f"inline handler at {filename}:{line}"
            return [handler_effects.raw_handler_of(form.handlers, scope, filename, label)]
        case _InstallKind.VALUE:
            return [handler_effects.element_of(form.handlers, scope, filename)]


@dataclass(frozen=True)
class _Wrapper:
    """``f(program)`` where ``f`` runs the Program it is given under its own handler
    without the reader seeing it run (``scheduled(body)``): read as an install."""

    handler: HandlerEffects
    program: _ProgramArg


class _Reader:
    """Reads function bodies into facts, once per function and bindings."""

    def __init__(self) -> None:
        self._facts: dict[_ReadKey, _Facts] = {}
        # Local functions being read (a local function calling itself is read once).
        self._local_reads: set[_Identity] = set()

    def facts(self, function: types.FunctionType, bound: _Bound = _NO_BINDINGS) -> _Facts:
        """What ``function`` does when read with ``bound`` (cached)."""
        key = _ReadKey(function, bound.key)
        known = self._facts.get(key)
        if known is not None:
            return known
        facts = _Facts()
        self._facts[key] = facts  # recursion sees an (empty) entry, not a loop
        located = _locate(function, bound)
        if isinstance(located, Unresolved):
            facts.unresolved.append((located.reason, located.text, located.location))
            return facts
        self.collect(
            _body_nodes(located.node),
            located.scope,
            located.filename,
            facts,
            generator=_is_generator(located.node),
        )
        return facts

    def collect(
        self,
        nodes: Iterable[ast.AST],
        scope: "_Scope",
        filename: str,
        facts: _Facts,
        *,
        generator: bool,
    ) -> None:
        for child in nodes:
            if isinstance(child, (ast.Yield, ast.YieldFrom)) and child.value is not None:
                self._performed(child.value, scope, filename, facts)
            elif not generator and isinstance(child, ast.Return) and child.value is not None:
                self._returned(child.value, scope, filename, facts)

    def is_program(self, function: types.FunctionType) -> bool:
        """A generator function, or a factory that returns a Program / effect."""
        if inspect.isgeneratorfunction(function):
            return True
        facts = self.facts(function)
        return bool(facts.effects or facts.calls or facts.handled)

    def _performed(self, expr: ast.expr, scope: _Scope, filename: str, facts: _Facts) -> None:
        """What performing ``expr`` (a yielded value) does: an effect, a Program call, a
        handled scope, a carried Program — or a place the reader cannot follow."""
        location = Location(filename, getattr(expr, "lineno", 0))
        if isinstance(expr, ast.IfExp):
            self._performed(expr.body, scope, filename, facts)
            self._performed(expr.orelse, scope, filename, facts)
            return
        if _adds_nothing(expr, scope):
            return
        if isinstance(expr, ast.Subscript):
            element = scope.resolve(expr)
            if isinstance(element, (_ProgramArg, _ProgramAnyOf)):
                self._performed_passed(element, facts)
                return
        if isinstance(expr, ast.Name):
            argument = scope.resolve(expr)
            if isinstance(argument, ReceivedEffect):
                facts.effects.append((argument.cls, location, True))
                return
            if isinstance(argument, (_ProgramArg, _ProgramAnyOf)):
                self._performed_passed(argument, facts)
                return
            opened = scope.local_values.get(expr.id)
            if opened is not None and _opening(opened, scope) is not None:
                # doeff-hy's bind: ``b.value if (b := open_bind(e)).__class__ is Pure else (yield b)``
                # — what is yielded is the bind it named (agora-redesign #844).
                self._performed(opened, scope, filename, facts)
                return
            choices = scope.local_choices.get(expr.id)
            if choices is not None:
                # A local assigned on several paths (Hy's ``match`` as an expression): it
                # holds whichever was assigned last — each is read, as both branches of a
                # conditional are (an assigned ``None`` performs nothing).
                for value in choices:
                    self._performed(value, scope, filename, facts)
                return
        call = self._as_call(expr, scope)
        if call is None:
            facts.unresolved.append(
                ("yielded a value that is not a call", ast.unparse(expr), location)
            )
            return
        self._performed_call(call, scope, filename, facts, location)

    def _performed_passed(self, passed: _ProgramArg | _ProgramAnyOf, facts: _Facts) -> None:
        """A Program the caller passed (or an element of the sequence it passed): it runs
        here, read where it was written — each element when the reader cannot tell which."""
        facts.performed_arguments.add(_identity(passed))
        for program in _programs_of(passed):
            facts.performed_arguments.add(_identity(program))
            self._performed(program.expr, program.scope, program.filename, facts)

    def _performed_call(
        self, call: ast.Call, scope: _Scope, filename: str, facts: _Facts, location: Location
    ) -> None:
        """A yielded call: a bind, an install, a ``(do! …)`` block, an effect, a Program call."""
        opening = _opening(call, scope)
        if opening is not None:
            self._opened(opening, scope, filename, facts, location)
            return
        form = _install_form(call, scope)
        if form is not None:
            handlers = _read_install(form, scope, filename)
            self._handled(handlers, form.body, scope, filename, facts, location)
            return
        block = _do_block(call, scope)
        if block is not None:
            inner = _scope_of(block, scope.module, parent=scope)
            self.collect(_body_nodes(block), inner, filename, facts, generator=True)
            return
        target = scope.resolve(call.func)
        if isinstance(target, _LocalFunction):
            self._local_call(target, call, scope, facts)
        elif target is UNBOUND:
            facts.unresolved.append(
                (
                    "yielded a call whose target is not a module-level name",
                    ast.unparse(call.func),
                    location,
                )
            )
        elif _is_effect_class(target):
            facts.effects.append((target, location, False))
            self._carried_arguments(target, call, scope, filename, facts)
        elif (function := _function_of(target)) is not None and not _is_control_class(target):
            self._program_call(target, function, call, scope, filename, facts, location)
        elif not _is_control_class(target):
            facts.unresolved.append(
                (
                    "yielded a call to something that is neither an effect nor a Program function",
                    ast.unparse(call.func),
                    location,
                )
            )

    def _local_call(
        self, local: _LocalFunction, call: ast.Call, scope: _Scope, facts: _Facts
    ) -> None:
        """A yielded call of a lambda / nested def: its body is read where it was written,
        its parameters bound to the arguments known here.  A generator body runs as the
        Program; any other body returns the Program that runs (a lambda's expression, a
        def's ``return``s)."""
        identity = _identity(local)
        if identity in self._local_reads:
            return  # a local function calling itself: its body is being read further up
        params = _positional_params(local.node)
        leading = itertools.takewhile(lambda a: not isinstance(a, ast.Starred), call.args)
        passed = [
            Binding(name, _argument_value(argument, scope))
            for name, argument in zip(params, leading, strict=False)
        ]
        bound = _Bound(tuple(b for b in passed if b.value is not UNBOUND))
        inner = _scope_of(local.node, local.scope.module, parent=local.scope, bound=bound)
        self._local_reads.add(identity)
        try:
            match local.node:
                case ast.Lambda(body=body):
                    self._performed(body, inner, local.filename, facts)
                case node if _is_generator(node):
                    self.collect(_body_nodes(node), inner, local.filename, facts, generator=True)
                case node:
                    for child in _body_nodes(node):
                        if isinstance(child, ast.Return) and child.value is not None:
                            self._performed(child.value, inner, local.filename, facts)
        finally:
            self._local_reads.discard(identity)

    def _opened(
        self,
        opening: _Opening,
        scope: _Scope,
        filename: str,
        facts: _Facts,
        location: Location,
    ) -> None:
        """``(<- x e)`` / ``(! e)``: ``e`` runs, and a declared effect's absent / failure
        answer is performed as Absent / Raise in this scope (ADR-DOE-CORE-EFFECTS-003 R13).
        ``:absent F`` wraps the one bind in a receiver turning every Absent inside into Raise."""
        inner = facts if opening.absent is None else _Facts()
        self._performed(opening.operand, scope, filename, inner)
        call = self._as_call(opening.operand, scope)
        target = UNBOUND if call is None else scope.resolve(call.func)
        if target is not UNBOUND and _is_effect_class(target):
            inner.effects.extend((answer, location, False) for answer in _opened_answers(target))
        if opening.absent is not None:
            receiver = replace(_absent_receiver(), name=f":absent {ast.unparse(opening.absent)}")
            text = ast.unparse(opening.operand)
            facts.handled.append(_HandledFacts((receiver,), inner, text, location))

    def _handled(
        self,
        handlers: Sequence[HandlerEffects],
        body: ast.expr,
        scope: _Scope,
        filename: str,
        facts: _Facts,
        location: Location,
    ) -> None:
        """``body`` run under ``handlers`` (outermost first): a handled scope."""
        inner = _Facts()
        self._performed(body, scope, filename, inner)
        facts.handled.append(_HandledFacts(tuple(handlers), inner, ast.unparse(body), location))

    def _program_call(
        self,
        target: Imported,
        function: types.FunctionType,
        call: ast.Call,
        scope: _Scope,
        filename: str,
        facts: _Facts,
        location: Location,
    ) -> None:
        """``f(...)`` for a Program function, followed with the arguments it is given.

        A Program argument ``f`` runs itself is read inside ``f``; one ``f`` runs under
        its own handler without the reader seeing it (``scheduled(body)``) makes a
        handled scope; the rest are carried by ``f``.
        """
        bound = _call_bindings(function, call, scope)
        performed = self._performed_arguments(function, bound)
        wrapper = _wrapper(target, call, bound, performed)
        if wrapper is not None:
            program = wrapper.program
            self._handled(
                [wrapper.handler], program.expr, program.scope, program.filename, facts, location
            )
            return
        facts.calls.append(_Call(function, bound, location))
        self._carried_arguments(target, call, scope, filename, facts, skip=performed)

    def _returned(self, expr: ast.expr, scope: _Scope, filename: str, facts: _Facts) -> None:
        """A Program factory's ``return f(...)`` / ``return E(...)``; other returns are data."""
        if isinstance(expr, ast.IfExp):
            self._returned(expr.body, scope, filename, facts)
            self._returned(expr.orelse, scope, filename, facts)
            return
        call = self._as_call(expr, scope)
        if call is None:
            return
        if _install_form(call, scope) is not None:
            self._performed(call, scope, filename, facts)
            return
        target = scope.resolve(call.func)
        if target is UNBOUND:
            return
        if _is_effect_class(target) or _function_of(target) is not None:
            self._performed(call, scope, filename, facts)

    def _performed_arguments(
        self, function: types.FunctionType, bound: _Bound
    ) -> frozenset[_Identity]:
        """The Program arguments in ``bound`` that ``function`` (transitively) runs where
        the reader sees what answers them — not beneath a handler it cannot read
        (``scheduled`` runs its body under ``core.prompt()``: read as a wrapper instead)."""
        arguments = {
            _identity(program)
            for binding in bound.bindings
            for program in _passed_programs(binding.value)
        }
        if not arguments:
            return frozenset()
        found: set[_Identity] = set()
        visited: set[_ReadKey] = set()
        pending = [self.facts(function, bound)]
        while pending:  # noqa: DOEFF012 - a depth-first walk over the call graph
            facts = pending.pop()
            found |= facts.performed_arguments & arguments
            for called in facts.calls:
                key = _ReadKey(called.function, called.bound.key)
                if key not in visited:
                    visited.add(key)
                    pending.append(self.facts(called.function, called.bound))
            pending.extend(
                scope.body
                for scope in facts.handled
                if all(handler.known for handler in scope.handlers)
            )
        return frozenset(found)

    def _carried_arguments(
        self,
        carrier: Imported,
        call: ast.Call,
        scope: _Scope,
        filename: str,
        facts: _Facts,
        *,
        skip: frozenset[_Identity] = frozenset(),
    ) -> None:
        """Programs handed to ``carrier`` in ``call`` (those the callee runs itself skipped),
        and the Program a function handed in a field ``carrier`` declares in
        ``__doeff_runs_carried__`` builds (``Traverse(f, items)`` — ``f``'s body)."""
        for argument in (*call.args, *(keyword.value for keyword in call.keywords)):
            location = Location(filename, getattr(argument, "lineno", 0))
            for program in self._carried_programs(argument, scope):
                if _identity(program) in skip:
                    continue
                body = _Facts()
                self._performed_passed(program, body)
                facts.carried.append(_Carried(carrier, body, _label_of(program), location))
        facts.carried.extend(
            built
            for argument in _declared_builders(carrier, call)
            if not self._carried_programs(argument, scope)
            and (built := self._carried_builder(carrier, argument, scope, filename)) is not None
        )

    def _carried_builder(
        self, carrier: Imported, argument: ast.expr, scope: _Scope, filename: str
    ) -> _Carried | None:
        """A function ``carrier`` calls to build the Program it runs where it was performed
        (``Traverse``'s ``f``): its body is that Program, read where the function was
        written with its parameters unbound — a ``do``-wrapped local def or lambda (what
        ``for/do`` expands to), a local def or lambda, or a Program function.  A value the
        reader cannot read as a function is not carried, as before (agora-redesign #2973)."""
        location = Location(filename, getattr(argument, "lineno", 0))
        function = _do_wrapped(argument, scope) or argument
        value = _argument_value(function, scope)
        if isinstance(value, _LocalFunction):
            body = _Facts()
            self._local_call(value, ast.Call(func=function, args=[], keywords=[]), scope, body)
        elif (program := _function_of(value)) is not None and self.is_program(program):
            body = _Facts(calls=[_Call(program, _NO_BINDINGS, location)])
        else:
            return None
        return _Carried(carrier, body, ast.unparse(argument), location)

    def _carried_programs(
        self, argument: ast.expr, scope: _Scope
    ) -> tuple[_ProgramArg | _ProgramAnyOf, ...]:
        """The Programs an argument hands over: one Program, or each element of a
        sequence of them (``f(#((serve-a) (serve-b)))`` — a callee that does not run
        them itself carries them)."""
        passed = _argument_value(argument, scope)
        if isinstance(passed, _ProgramSeq):
            return passed.elements
        program = self._carried_program(argument, scope)
        return () if program is None else (program,)

    def _carried_program(
        self, argument: ast.expr, scope: _Scope
    ) -> _ProgramArg | _ProgramAnyOf | None:
        """The Program an argument hands over: a Program the caller passed on (or an
        element of a sequence it passed), a Program call, or an install form (an effect
        or plain data is not carried).  A conditional (``a() if c else b()``) carries
        itself when either branch is a Program: performing it reads both branches
        (``_performed``)."""
        if isinstance(argument, ast.IfExp):
            branches = (argument.body, argument.orelse)
            if any(self._carried_program(branch, scope) is not None for branch in branches):
                return _ProgramArg(argument, scope)
            return None
        if isinstance(argument, (ast.Name, ast.Subscript)):
            value = scope.resolve(argument)
            if isinstance(value, (_ProgramArg, _ProgramAnyOf)):
                return value
        inner = self._as_call(argument, scope)
        if inner is None:
            return None
        if _install_form(inner, scope) is not None:
            return _ProgramArg(argument, scope)
        target = scope.resolve(inner.func)
        if target is UNBOUND or _is_effect_class(target):
            return None
        function = _function_of(target)
        if function is None or not self.is_program(function):
            return None
        return _ProgramArg(argument, scope)

    @staticmethod
    def _as_call(expr: ast.expr, scope: _Scope) -> ast.Call | None:
        """``expr`` as a call, through a local bound once to one (``x = f(); yield x``)."""
        if isinstance(expr, ast.Call):
            return expr
        if isinstance(expr, ast.Name) and expr.id in scope.local_calls:
            return scope.local_calls[expr.id]
        return None


def _adds_nothing(expr: ast.expr, scope: _Scope) -> bool:
    """A yielded value whose performing adds nothing here: ``None``, or the Program the
    received effect carried (``yield effect.program`` — read where the effect was
    performed, see ``ReceivedField``), or the Program a function field it declares in
    ``__doeff_runs_carried__`` builds (``yield effect.f(item)`` — see ``_builds_carried``)."""
    match expr:
        case ast.Constant(value=None):
            return True
        case ast.Name() | ast.Attribute():
            return isinstance(scope.resolve(expr), ReceivedField)
        case ast.Call(func=func):
            return _builds_carried(scope.resolve(func))
        case _:
            return False


def _wrapper(
    target: Imported, call: ast.Call, bound: _Bound, performed: frozenset[_Identity]
) -> _Wrapper | None:
    """``f(program)`` read as an install when ``f`` does not visibly run the one Program
    it is given but reads as a handler (``scheduled`` — the scheduler's clauses)."""
    programs = [b.value for b in bound.bindings if isinstance(b.value, _ProgramArg)]
    if len(programs) != 1 or _identity(programs[0]) in performed:
        return None
    from doeff_effect_analyzer import handler_effects

    handler = handler_effects.analyze_handler(target, name=ast.unparse(call.func))
    return _Wrapper(handler, programs[0]) if handler.known else None


def _is_generator(node: FunctionNode) -> bool:
    """Whether the body yields (a Program body), as opposed to returning one."""
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _body_nodes(node))


@dataclass(frozen=True)
class _Located:
    """A function's definition in its module's AST, with the names it sees."""

    node: FunctionNode
    scope: _Scope
    filename: str


def _locate(function: types.FunctionType, bound: _Bound = _NO_BINDINGS) -> _Located | Unresolved:
    """The def (or lambda) of ``function`` in its module's (expanded) AST, with its name scope.

    A method (``Cls.meth``) is read with its first parameter bound to an instance
    of ``Cls``, so ``self.other(...)`` resolves to the class's method.
    """
    module = sys.modules.get(function.__module__)
    if module is None:
        return Unresolved("module not imported", function.__module__, _location_of(function))
    try:
        source = _module_source(module)
    except (OSError, ValueError, TypeError, SyntaxError) as error:
        return Unresolved("source unavailable", str(error), _location_of(function))
    node = source.definitions.find(function)
    if node is None:
        return Unresolved(
            "definition not found in source", function.__qualname__, _location_of(function)
        )
    scope = _scope_of(node, module, bound=_method_self(function, module, node).plus(bound))
    return _Located(node, scope, source.filename)


def _method_self(
    function: types.FunctionType, module: types.ModuleType, node: FunctionNode
) -> _Bound:
    """A method's ``self`` bound to an instance of its class (so ``self.m()`` resolves)."""
    parts = function.__qualname__.split(".")
    params = _positional_params(node)
    if len(parts) < 2 or "<locals>" in parts or not params:
        return _NO_BINDINGS
    owner: Any = module
    for part in parts[:-1]:
        owner = getattr(owner, part, None)
    if not isinstance(owner, type) or isinstance(
        inspect.getattr_static(owner, parts[-1], None), staticmethod
    ):
        return _NO_BINDINGS
    return _Bound((Binding(params[0], _Instance(owner)),))


def _location_of(function: types.FunctionType) -> Location:
    return Location(function.__code__.co_filename, function.__code__.co_firstlineno)


def _find_function(tree: ast.Module, function: types.FunctionType) -> FunctionNode | None:
    """The def of ``function`` in ``tree`` (qualname path; the closest line on ties).

    A ``lambda`` (``<lambda>`` in the qualname — ``defhandler`` compiles a handler whose
    only clause is ``(resume v)`` to one) is matched by line and parameter names.
    """
    candidates: list[FunctionNode] = [
        definition
        for definition in _definitions_by_path(tree).get(_qualname_path(function), ())
        if not isinstance(definition, ast.ClassDef)
    ]
    if not candidates:
        return None
    code = function.__code__
    return min(
        candidates,
        key=lambda node: _closeness(_definition_lines(node), _definition_params(node), code),
    )


def _qualname_path(function: types.FunctionType) -> tuple[str, ...]:
    """The name path a function's def is indexed under (its qualname without ``<locals>``)."""
    return tuple(part for part in function.__qualname__.split(".") if part != "<locals>")


def _definition_lines(node: FunctionNode) -> tuple[int, ...]:
    """The lines a def may be reported at — its own and its decorators' (a code object's
    first line is the first decorator's)."""
    decorators = [] if isinstance(node, ast.Lambda) else node.decorator_list
    return (node.lineno, *(decorator.lineno for decorator in decorators))


def _definition_params(node: FunctionNode) -> tuple[str, ...]:
    """The positional parameter names a def declares (compared with the code object's)."""
    return tuple(a.arg for a in (*node.args.posonlyargs, *node.args.args))


def _closeness(lines: tuple[int, ...], params: tuple[str, ...], code: types.CodeType) -> "_Closeness":
    """How far a candidate def is from the code object (line first, then parameters) — the
    one measure both the whole tree and the cached index choose by."""
    same_params = params == tuple(code.co_varnames[: code.co_argcount])
    return _Closeness(min(abs(line - code.co_firstlineno) for line in lines), 0 if same_params else 1)


_DEFINITION_INDEX: dict[int, tuple[ast.Module, dict[tuple[str, ...], list[ast.AST]]]] = {}


def _definitions_by_path(tree: ast.Module) -> dict[tuple[str, ...], list[ast.AST]]:
    """Every def / class / lambda of ``tree`` by its name path, built once per tree.

    ``_find_function`` runs for every function the reader follows; walking the whole
    module each time was most of an analysis once expansion was cached. Entries keep
    depth-first order, so ties in ``_find_function`` resolve as before.
    """
    cached = _DEFINITION_INDEX.get(id(tree))
    if cached is not None and cached[0] is tree:
        return cached[1]
    index: dict[tuple[str, ...], list[ast.AST]] = {}

    def walk(members: Iterable[ast.AST], path: tuple[str, ...]) -> None:
        for definition in _direct_definitions(members):
            name = "<lambda>" if isinstance(definition, ast.Lambda) else definition.name
            here = (*path, name)
            index.setdefault(here, []).append(definition)
            walk([definition.body] if isinstance(definition, ast.Lambda) else definition.body, here)

    walk(tree.body, ())
    _DEFINITION_INDEX[id(tree)] = (tree, index)
    return index


@dataclass(frozen=True, order=True)
class _Closeness:
    """Lines between a candidate def and the code object; then 1 when parameters differ
    (two lambdas on one line — ``handle`` inside ``defhandler`` — tell apart by those)."""

    lines: int
    params: int


def _direct_definitions(
    members: Iterable[ast.AST],
) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda]:
    """Defs, classes and lambdas in ``members`` that belong to this scope (not nested in another)."""
    stack: list[ast.AST] = list(reversed(list(members)))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield node
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


# --------------------------------------------------------------------------- report


def analyze_program(target: Any, *, bindings: Mapping[str, Any] | None = None) -> ProgramEffects:
    """Effects of a Program function (object, or ``"module:attr"``), following calls.

    ``bindings`` binds the target's parameters (``{"foundation": production_handlers}``)
    so what the body does with them is read; a ``functools.partial`` target binds
    its arguments the same way.  The target's module is imported (to resolve
    names); no Program is run.
    """
    obj = resolve_target(target) if isinstance(target, str) else target
    positional: tuple[Any, ...] = ()
    keywords: dict[str, Any] = {}
    if isinstance(obj, functools.partial):
        positional, keywords, obj = obj.args, dict(obj.keywords), obj.func
    function = _function_of(obj)
    if function is None:
        raise TypeError(f"{target!r} is not a Program function (@do / defk / generator)")
    bound = bindings_for(function, positional=positional, keywords={**keywords, **(bindings or {})})
    return _report(
        _Reader(),
        function,
        bound,
        label=str(target) if isinstance(target, str) else qualified_name(function),
    )


def _report(
    reader: _Reader,
    root: types.FunctionType,
    bound: _Bound,
    *,
    label: str,
    active: frozenset[_ReadKey] = frozenset(),
) -> ProgramEffects:
    """The report of one Program function read with ``bound``."""
    key = _ReadKey(root, bound.key)
    return _report_facts(reader, reader.facts(root, bound), label=label, active=active | {key})


def _report_facts(
    reader: _Reader,
    start: _Facts,
    *,
    label: str,
    active: frozenset[_ReadKey] = frozenset(),
) -> ProgramEffects:
    """Effects of ``start`` and of every Program function it calls (transitively).

    ``active`` = the functions being reported further up (a recursion through a
    handled scope or a carrier stops there — its effects are already counted).
    """
    effects: list[EffectUse] = []
    carried: list[CarriedProgram] = []
    unresolved: list[Unresolved] = []
    handled: list[HandledProgram] = []
    seen: set[_ReadKey] = set(active)

    def absorb(facts: _Facts, via: tuple[str, ...], path: frozenset[_ReadKey]) -> None:
        """Add one body's facts, then the bodies it calls (each once)."""
        effects.extend(
            EffectUse(effect, location, via, forwarded)
            for effect, location, forwarded in facts.effects
        )
        unresolved.extend(
            Unresolved(reason, text, location, via) for reason, text, location in facts.unresolved
        )
        for item in facts.carried:
            # A carried Program that recurses into one being reported (``path``) stops
            # there: its calls into ``path`` are skipped.
            program = _report_facts(reader, item.body, label=item.label, active=path)
            carried.append(
                CarriedProgram(
                    carrier=item.carrier, program=program, location=item.location, via=via
                )
            )
        for scope in facts.handled:
            handled.append(
                HandledProgram(
                    handlers=scope.handlers,
                    program=_report_facts(reader, scope.body, label=scope.text, active=path),
                    location=scope.location,
                    via=via,
                )
            )
        for call in facts.calls:
            call_key = _ReadKey(call.function, call.bound.key)
            if call_key in seen:
                continue
            seen.add(call_key)
            absorb(
                reader.facts(call.function, call.bound),
                (*via, qualified_name(call.function)),
                path | {call_key},
            )

    absorb(start, (), active)
    return ProgramEffects(
        target=label,
        effects=tuple(effects),
        carried=tuple(carried),
        unresolved=tuple(unresolved),
        handled=tuple(handled),
    )
