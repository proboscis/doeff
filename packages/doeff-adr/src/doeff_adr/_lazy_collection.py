"""Collect Hy test files without importing them, and import them for the first time in the item setup (parent #1211).

Importing a test module during collection runs the module's top-level code, which takes 10 seconds for one file (measured in #1211). So:

- **Collection**: the records that the item-building macro piled onto the module in its expansion (``doeff_hy._pytest_items``) are looked up
    in the doeff-adr cache (``item_cache`` — the key is the hash of the source contents plus the macro records used by the expansion, #1291),
    and a stub module is built from stub functions that have the same arguments, marks and parametrize as the records. The stub module is handed to pytest's Module collection, so nodeid,
    parametrize ids and marks are produced by the same pytest procedure as when the module was imported and collected.
- **Files not in the cache** are imported during collection as before. They are saved to the cache only when the records of the imported module
    fully explain the real module (``verify_records`` — dynamic records are filled in from real values, there are no tests, unsupported fixtures or xunit functions missing from the records, and the mark shapes match).
    Files skipped as a whole module end with the import Skipped, so they are not saved. The reasons are reported at the end of collection.
- **setup**: before the item's fixtures, the file's module is read exactly once through the normal import path, and the item's function, marks and
    parametrize values are replaced with the real ones. If the real module disagrees with the records, the item is turned red and the records are deleted (no silently running on a stale item).
"""

import importlib
import importlib.util
import inspect
import sys
import types
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import pytest
from _pytest.fixtures import FixtureFunctionDefinition, getfixturemarker
from _pytest.mark.structures import Mark as PytestMark
from _pytest.mark.structures import ParameterSet, get_unpacked_marks
from doeff_hy import MalformedRecord
from doeff_hy._pytest_items import (
    Decorator,
    Dynamic,
    FunctionItem,
    LiteralValue,
    Mark,
    ModuleMarks,
    OpaqueValue,
    Parametrize,
    ParamValue,
    Record,
    SkipIf,
    decode_records,
    module_record_texts,
)
from hy.importer import HyLoader

from doeff_adr._item_cache import (
    CacheHit,
    CacheMiss,
    FixtureRecord,
    MalformedCacheEntry,
    fixture_scope,
    read_cached,
)
from doeff_adr._runtime_records import (
    OpaqueParam,
    UnrecordableError,
    parametrize_record,
    runtime_function_record,
)
from doeff_adr._source_dependencies import DependencyChecks

# Functions in pytest's xunit shape (their names carry meaning — the stub module has none, so if present the file is imported and collected).
XUNIT_NAMES = frozenset(
    {
        "setup_module",
        "teardown_module",
        "setUpModule",
        "tearDownModule",
        "setup_function",
        "teardown_function",
        "pytest_plugins",
        "pytest_generate_tests",
        "__test__",
    }
)


@dataclass(frozen=True)
class Indexed:
    """A file explained by the records — collected through a stub module."""

    records: tuple[Record, ...]
    fixtures: tuple[FixtureRecord, ...]


@dataclass(frozen=True)
class NeedsImport:
    """A file the records cannot explain — imported during collection. ``reason`` is reported at the end of collection."""

    reason: str


CollectionPlan = Indexed | NeedsImport


def plan_collection(
    path: Path, cache_dir: Path, root: Path, checks: DependencyChecks
) -> CollectionPlan:
    """Decide from the cache alone whether the file can be collected without importing (the cache only holds records already checked against the real module when saved)."""
    match read_cached(path, cache_dir, root, checks):
        case CacheHit(records, fixtures):
            return Indexed(records, fixtures)
        case CacheMiss(reason):
            return NeedsImport(reason)


@dataclass(frozen=True)
class Verified:
    """Result of checking the imported module against the records — it may be saved only when ``problems`` is empty. ``fixtures`` are the fixture records to save."""

    problems: list[str]
    fixtures: tuple[FixtureRecord, ...]
    records: tuple[Record, ...] = ()
    dynamic: bool = False


def _fixture_record(attribute: str, value: FixtureFunctionDefinition) -> FixtureRecord | str:
    """Turn one fixture of the module into a record. For a fixture that changes the collection result (params, autouse, scope decided by a call), return a reason sentence."""
    marker = getfixturemarker(value)
    if marker is None:
        return f"cannot read the mark of fixture {attribute}"
    if marker.params is not None or marker.autouse:
        return f"fixture {attribute} has params / autouse"
    try:
        scope = fixture_scope(marker.scope)
    except MalformedCacheEntry:
        return f"scope of fixture {attribute} is not a word (scope decided by a call)"
    function = inspect.unwrap(value)
    return FixtureRecord(
        attribute=attribute,
        name=marker.name or attribute,
        scope=scope,
        argnames=tuple(inspect.signature(function).parameters),
        generator=inspect.isgeneratorfunction(function),
    )


@dataclass(frozen=True)
class ResolvedRecords:
    records: list[Record]
    problems: list[str]


def _resolve_dynamic_records(
    records: list[Record], real_module: types.ModuleType
) -> ResolvedRecords:
    """Fill in only the dynamic records with real values, and return a reason for shapes that cannot be saved."""
    resolved: list[Record] = []
    problems: list[str] = []
    for record in records:
        if isinstance(record, Dynamic):
            function: object = vars(real_module).get(record.where)
            if not callable(function):
                problems.append(f"dynamic: {record.where} ({record.reason})")
                continue
            try:
                resolved.append(runtime_function_record(record.where, function))
            except UnrecordableError as exc:
                problems.append(f"dynamic: {record.where} ({exc})")
        else:
            resolved.append(record)
    return ResolvedRecords(resolved, problems)


def _last_function_records(records: list[Record]) -> list[Record]:
    # As with module assignment, keep the last value at the first insertion position. Dynamic also replaces a function of the same name.
    unique: dict[str, Record] = {}
    for position, record in enumerate(records):
        if isinstance(record, FunctionItem):
            key = record.name
        elif isinstance(record, Dynamic):
            key = record.where
        else:
            key = f"<module-marks-{position}>"
        unique[key] = record
    return list(unique.values())


def _verify_module_attributes(
    real_module: types.ModuleType, names: set[str], name_matches: Callable[[str], bool]
) -> Verified:
    problems: list[str] = []
    fixtures: list[FixtureRecord] = []
    for name, value in vars(real_module).items():
        if name in names:
            continue
        if name in XUNIT_NAMES:
            problems.append(f"xunit {name} is present")
        elif isinstance(value, FixtureFunctionDefinition):
            match _fixture_record(name, value):
                case FixtureRecord() as record:
                    fixtures.append(record)
                case reason:
                    problems.append(reason)
        elif getfixturemarker(value) is not None:
            problems.append(f"cannot read the shape of fixture {name}")
        elif name_matches(name) and callable(value):
            problems.append(f"a test-like name {name} is not in the records")
    return Verified(problems, tuple(fixtures))


def verify_records(real_module: types.ModuleType, name_matches: Callable[[str], bool]) -> Verified:
    """Check that the records after import fully explain the functions, marks and fixtures pytest collects."""
    try:
        records = decode_records(module_record_texts(real_module))
    except MalformedRecord as exc:
        return Verified([f"the shape of the records is wrong: {exc}"], ())
    records = _last_function_records(records)
    dynamic: bool = any(isinstance(r, Dynamic) for r in records)
    resolved = _resolve_dynamic_records(records, real_module)
    records, problems = resolved.records, resolved.problems
    functions = [r for r in records if isinstance(r, FunctionItem)]
    names = {r.name for r in functions}
    for record in functions:
        real = vars(real_module).get(record.name)
        if real is None:
            problems.append(f"record {record.name} is not in the module")
            continue
        if inspect.iscoroutinefunction(real) or inspect.isasyncgenfunction(real):
            problems.append(f"async {record.name} is imported during collection")
        stub = _stub_function(record, Path(real_module.__file__ or ""), real_module.__name__)
        try:
            _check_marks(get_unpacked_marks(stub), get_unpacked_marks(real), record.name)
        except (RecordMismatch, UnrecordableError) as exc:
            problems.append(str(exc))
    module_marks = [r for r in records if isinstance(r, ModuleMarks)]
    recorded_marks = [name for r in module_marks for name in r.names]
    unpacked_module_marks = get_unpacked_marks(real_module)
    if any(
        mark.name not in {"skip", "skipif"} and (mark.args or mark.kwargs)
        for mark in unpacked_module_marks
    ):
        problems.append("a module mark with arguments is imported during collection")
    real_marks = [m.name for m in unpacked_module_marks]
    if recorded_marks != real_marks:
        problems.append(f"module marks differ from the records — records {recorded_marks} / real {real_marks}")
    attributes = _verify_module_attributes(real_module, names, name_matches)
    problems.extend(attributes.problems)
    return Verified(problems, attributes.fixtures, tuple(records), dynamic)


# ---------------------------------------------------------------------------
# Stub module (built from the records — used only for collection, replaced by the real one at setup)
# ---------------------------------------------------------------------------


def _param_value(value: ParamValue, position: int) -> object:
    """Turn the params values of the records into stub values to which pytest gives the same id (a literal is the very same value as the real one)."""
    match value:
        case LiteralValue(v):
            return v
        case OpaqueValue():
            return OpaqueParam(position)


def _stub_mark(decorator: Decorator) -> pytest.MarkDecorator:
    """Turn one decorator of the records into a pytest mark that makes the same name and the same id as the real one."""
    match decorator:
        case Parametrize(argnames, values, ids):
            return pytest.mark.parametrize(
                argnames, [_param_value(v, position) for position, v in enumerate(values)], ids=ids
            )
        case Mark(name):
            return getattr(pytest.mark, name)
        case SkipIf():
            # The condition is a run-time expression — the skipping plugin evaluates it after setup replaces it with the real mark.
            return pytest.mark.skipif(False, reason="(the condition is evaluated with the real mark after import)")


_StubResult = TypeVar("_StubResult")


class SignatureFunction(Generic[_StubResult]):
    """A callable carrying the function attributes pytest reads. Dynamic attributes are not set through a dict.

    __wrapped__ points to the original function. code and defaults are also exposed to keep inspect's generator detection.
    """

    __signature__: inspect.Signature

    def __init__(self, function: Callable[..., _StubResult]) -> None:
        if not isinstance(function, types.FunctionType):
            raise TypeError("a stub function needs a Python function")
        self.__wrapped__: Callable[..., _StubResult] = function
        self._function = function
        self.__name__ = function.__name__
        self.__qualname__ = function.__qualname__
        self.__module__ = function.__module__
        self.__defaults__: tuple[object, ...] | None = function.__defaults__
        self.__kwdefaults__: dict[str, object] | None = function.__kwdefaults__

    @property
    def __code__(self) -> types.CodeType:
        return self._function.__code__

    def locate_source(self, path: Path, name: str) -> None:
        """Align pytest's report location with the original test file of the records."""
        self._function.__code__ = self._function.__code__.replace(
            co_filename=str(path), co_firstlineno=1, co_name=name
        )

    def __call__(self, *args: object, **kwargs: object) -> _StubResult:
        return self.__wrapped__(*args, **kwargs)


class RecordedModule(types.ModuleType):
    """A module derived from the records, on which pytest's module marks can be set directly."""

    pytestmark: list[pytest.MarkDecorator]


def _stub_function(item: FunctionItem, path: Path, module_name: str) -> Callable[..., None]:
    """From one function of the records, build a stub function with the same name, arguments and marks (handed to pytest's fixture and parametrize expansion)."""

    def body(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(
            f"{module_name}.{item.name}: a stub function built from the records was called (setup should have replaced it with the real one)"
        )

    stub = SignatureFunction(body)
    # The hook that shows the stub function's arguments to pytest (inspect.signature reads __signature__).
    stub.__signature__ = inspect.Signature(
        [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in item.argnames]
    )
    stub.__name__ = stub.__qualname__ = item.name
    stub.__module__ = module_name
    # The report location (pytest's reportinfo) points to the test file. The records have no line, so it is 1.
    stub.locate_source(path, item.name)
    marked: Callable[..., None] = stub
    for decorator in reversed(item.decorators):
        marked = _stub_mark(decorator)(marked)
    return marked


def _stub_module_mark(name: str) -> pytest.MarkDecorator:
    """From the name of a module mark, build a stub mark that collection's ``-m`` reads the same way (the arguments are replaced by the real mark at setup).

    skipif / skip without arguments are read as marks that skip unconditionally, so put a stub condition that does not skip until setup.
    """
    match name:
        case "skipif":
            return pytest.mark.skipif(False, reason="(the condition is evaluated with the real mark after import)")
        case _:
            return getattr(pytest.mark, name)


def _stub_fixture(record: FixtureRecord, module_name: str) -> FixtureFunctionDefinition:
    """From one fixture of the records, build a stub fixture with the same name, scope and arguments. When called, it calls the original function of the real
    module's fixture, already imported at setup (the meaning of a fixture exists only in the real module)."""

    def real_function() -> Callable[..., object]:
        """The original function of the real module's fixture (the item's setup has already imported the real module)."""
        real_module = sys.modules.get(module_name)
        if real_module is None:
            raise RuntimeError(
                f"{module_name}: stub fixture {record.name} was called before the real module was imported"
            )
        return inspect.unwrap(vars(real_module)[record.attribute])

    if record.generator:

        def generator_stub(**kwargs: object) -> Generator[object, None, None]:
            result: object = real_function()(**kwargs)
            if not isinstance(result, Generator):
                raise TypeError(
                    f"{module_name}.{record.attribute}: the generator fixture did not return a Generator"
                )
            yield from result

        function: Callable[..., object] = generator_stub
    else:

        def value_stub(**kwargs: object) -> object:
            return real_function()(**kwargs)

        function = value_stub
    stub = SignatureFunction(function)
    # The hook that shows the stub fixture's arguments to pytest (inspect.signature reads __signature__).
    stub.__signature__ = inspect.Signature(
        [
            inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            for name in record.argnames
        ]
    )
    stub.__name__ = stub.__qualname__ = record.attribute
    stub.__module__ = module_name
    return pytest.fixture(scope=record.scope, name=record.name)(stub)


def stub_module(
    records: tuple[Record, ...], fixtures: tuple[FixtureRecord, ...], path: Path, module_name: str
) -> types.ModuleType:
    """Build a stub module from the records — it holds only what pytest's Module collection reads (functions, pytestmark, fixtures)."""
    module = RecordedModule(module_name)
    module.__file__ = str(path)
    for fixture in fixtures:
        setattr(module, fixture.attribute, _stub_fixture(fixture, module_name))
    for record in records:
        match record:
            case FunctionItem():
                setattr(module, record.name, _stub_function(record, path, module_name))
            case ModuleMarks(names):
                module.pytestmark = [_stub_module_mark(name) for name in names]
            case Dynamic():
                raise AssertionError(
                    "A file with Dynamic records is sent to import by plan_collection"
                )
    return module


# ---------------------------------------------------------------------------
# setup — switch to the real module and check against the records
# ---------------------------------------------------------------------------


class RecordMismatchError(Exception):
    """The real module disagreed with the records."""


RecordMismatch = RecordMismatchError


def _shape(marks: Iterable[PytestMark]) -> list[tuple[str, object]]:
    """Turn a sequence of marks into a form that can be checked against the records (the name, and for parametrize the argument names, the number of values and literal values)."""
    shape: list[tuple[str, object]] = []
    for mark in marks:
        match mark.name:
            case "parametrize":
                spec: Parametrize = parametrize_record(mark)
                # In Python 1 == True == 1.0, but the pytest id and the type of the real value differ.
                literal_types = tuple(
                    type(value.value) if isinstance(value, LiteralValue) else None
                    for value in spec.values
                )
                shape.append(("parametrize", (spec, literal_types)))
            case "skipif":
                shape.append(("skipif", None))
            case name:
                shape.append((name, None))
    return shape


def _check_marks(stub_marks: list[PytestMark], real_marks: list[PytestMark], where: str) -> None:
    """Check that the marks of the stub function and the real function are the same within what the records can say."""
    try:
        stub_shape = _shape(stub_marks)
        real_shape = _shape(real_marks)
    except UnrecordableError as exc:
        raise RecordMismatch(f"{where}: cannot check the real marks: {exc}") from exc
    if stub_shape != real_shape:
        raise RecordMismatch(f"{where}: marks differ from the records — records {stub_shape} / real {real_shape}")


def import_module_for(path: Path, module_name: str) -> types.ModuleType:
    """Load the module of a test file. The parent package is loaded first by a normal import; the test module is loaded from this file and attached to the parent.

    If it were put into ``sys.modules`` without loading the parent, another test would fail when it looks up the name with ``(import pkg.sub.mod :as …)``.
   The test module itself is loaded from the file because, as with a workspace's per-package ``tests``,
    a parent of the same name may exist in another dir, and this file is still the one to load (it is not attached to the parent then).
    """
    path = path.resolve()
    existing = sys.modules.get(module_name)
    if existing is not None and Path(existing.__file__ or "").resolve() == path:
        return existing
    parent_name, _, leaf = module_name.rpartition(".")
    parent = importlib.import_module(parent_name) if parent_name else None
    owns = parent is not None and path.parent in {
        Path(entry).resolve() for entry in getattr(parent, "__path__", [])
    }
    importlib.invalidate_caches()
    loader = HyLoader(module_name, str(path))
    spec = importlib.util.spec_from_file_location(module_name, path, loader=loader)
    if spec is None:
        raise ImportError(f"could not create import spec for executable ADR: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    if owns:
        setattr(parent, leaf, module)
    return module


def swap_in_real_function(item: pytest.Function, real_module: types.ModuleType) -> None:
    """Replace the item's stub function with the real one: function, marks, parametrize values. On mismatch, RecordMismatch."""
    real = getattr(real_module, item.originalname, None)
    if real is None:
        raise RecordMismatch(f"{item.nodeid}: the real module has no {item.originalname}")
    stub_marks = get_unpacked_marks(item.obj)
    real_marks = get_unpacked_marks(real)
    _check_marks(stub_marks, real_marks, item.nodeid)
    callspec = getattr(item, "callspec", None)
    if callspec is not None:
        real_values = {
            mark.args[0]: list(mark.args[1]) for mark in real_marks if mark.name == "parametrize"
        }
        for argname, value in list(callspec.params.items()):
            if isinstance(value, OpaqueParam):
                actual: object = real_values[argname][value.position]
                callspec.params[argname] = (
                    actual.values[0] if isinstance(actual, ParameterSet) else actual
                )
    item.obj = real
    _replace_stub_marks(item, stub_marks, real_marks)


def swap_in_real_module_marks(
    node: pytest.Module, stub_module_obj: types.ModuleType, real_module: types.ModuleType
) -> None:
    """Replace the module node's marks (attached from the stub module's pytestmark) with the real module's marks. On a name differing from the records, RecordMismatch."""
    stub_marks = get_unpacked_marks(stub_module_obj)
    real_marks = get_unpacked_marks(real_module)
    if [m.name for m in stub_marks] != [m.name for m in real_marks]:
        raise RecordMismatch(
            f"{node.nodeid}: module marks differ from the records — records {[m.name for m in stub_marks]} / real {[m.name for m in real_marks]}"
        )
    _replace_stub_marks(node, stub_marks, real_marks)


def _replace_stub_marks(
    node: pytest.Item | pytest.Collector, stub_marks: list[PytestMark], real_marks: list[PytestMark]
) -> None:
    """Replace only the stub marks of the node (the same object) with the real marks at the same position in the sequence.

    Marks added by a hook (such as ``pytest_collection_modifyitems``) via ``add_marker`` after collection, and marks from parametrize values, are
    left in place, position and all — the sequence of marks and ``keywords`` become the same as when the module was imported and collected.
    """
    real_of = {id(stub): real for stub, real in zip(stub_marks, real_marks, strict=True)}
    node.own_markers = [real_of.get(id(mark), mark) for mark in node.own_markers]
    for stub, real in zip(stub_marks, real_marks, strict=True):
        if node.keywords.get(stub.name) is stub:
            node.keywords[stub.name] = real


def check_no_unrecorded_items(
    module_names: Iterable[str],
    real_module: types.ModuleType,
    name_matches: Callable[[str], bool],
    where: str,
) -> None:
    """Check that the real module has no test-like name missing from the records (if it does, that item was missing from the stub module)."""
    recorded = set(module_names)
    extra = sorted(
        name
        for name, value in vars(real_module).items()
        if name_matches(name)
        and callable(value)
        and name not in recorded
        and getattr(value, "__module__", None) == real_module.__name__
    )
    if extra:
        raise RecordMismatch(f"{where}: there is a test not in the records — {extra}")
