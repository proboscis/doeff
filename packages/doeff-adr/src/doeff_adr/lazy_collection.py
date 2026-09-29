"""Hy の test file を import せずに収集し、item の setup で初めて import する(agora-redesign #1223・親 #1211)。

収集で test module を import すると module の最上位の実行が走り、1 file で 10 秒かかる(#1211 の実測)。そこで:

- **収集**: item を作る macro が展開の式で module に積んだ記録(``doeff_hy.pytest_items``)を、doeff-adr のキャッシュ
  (``item_cache`` — 鍵は source の内容の hash と macro の提供元の内容の hash・#1291)から引き、記録と同じ引数・印・
  parametrize を持つ仮の関数を並べた仮の module を作る。その仮の module を pytest の Module の収集に渡すので、nodeid・
  parametrize の id・印は import して収集した時と同じ pytest の手順で作られる。
- **キャッシュに無い file** は、今までどおり収集で import する。import した module の記録が実物を全部説明する時
  (``verify_records`` — 動的な記録を実値から補い、記録に無いtest・未対応fixture・xunitの関数が無く、印の形が同じ)だけキャッシュに
  保存する。module ごと飛ばす file は import が Skipped で終わるので保存されない。理由は収集の終わりに報告する。
- **setup**: item の fixture より先に、その file の module を通常の import の経路で 1 度だけ読み、item の関数・印・
  parametrize の値を実物に替える。実物が記録と食い違えば、その item を赤にし、記録を消す(黙って古い item で走らない)。
"""

import importlib
import importlib.util
import inspect
import sys
import types
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from _pytest.fixtures import FixtureFunctionDefinition, getfixturemarker
from _pytest.mark.structures import Mark as PytestMark
from _pytest.mark.structures import ParameterSet, get_unpacked_marks
from doeff_hy.pytest_items import (
    Decorator,
    Dynamic,
    FunctionItem,
    LiteralValue,
    MalformedRecord,
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

from doeff_adr.item_cache import (
    CacheHit,
    CacheMiss,
    FixtureRecord,
    MalformedCacheEntry,
    fixture_scope,
    read_cached,
)
from doeff_adr.runtime_records import (
    OpaqueParam,
    UnrecordableError,
    parametrize_record,
    runtime_function_record,
)
from doeff_adr.source_dependencies import DependencyChecks

# pytest の xunit の形の関数(名で意味を持つ — 仮の module は持たないので、あれば import して収集する)。
XUNIT_NAMES = frozenset(
    {
        "setup_module",
        "teardown_module",
        "setUpModule",
        "tearDownModule",
        "setup_function",
        "teardown_function",
        "pytest_plugins",
    }
)


@dataclass(frozen=True)
class Indexed:
    """記録で説明できる file — 仮の module で収集する。"""

    records: tuple[Record, ...]
    fixtures: tuple[FixtureRecord, ...]


@dataclass(frozen=True)
class NeedsImport:
    """記録で説明できない file — 収集で import する。``reason`` は収集の終わりに報告する。"""

    reason: str


CollectionPlan = Indexed | NeedsImport


def plan_collection(path: Path, cache_dir: Path, root: Path, checks: DependencyChecks) -> CollectionPlan:
    """収集で import せずに済むかを、キャッシュだけから決める(保存の時に実物と突き合わせ済みの記録しか入っていない)。"""
    match read_cached(path, cache_dir, root, checks):
        case CacheHit(records, fixtures):
            return Indexed(records, fixtures)
        case CacheMiss(reason):
            return NeedsImport(reason)


@dataclass(frozen=True)
class Verified:
    """import した module と記録の突き合わせの結果 — ``problems`` が空の時だけ保存してよい。``fixtures`` は保存する fixture の記録。"""

    problems: list[str]
    fixtures: tuple[FixtureRecord, ...]
    records: tuple[Record, ...] = ()
    dynamic: bool = False


def _fixture_record(attribute: str, value: FixtureFunctionDefinition) -> FixtureRecord | str:
    """module の fixture 1 つを記録にする。収集の結果を変える fixture(params・autouse・呼び出しで決まる scope)は理由の文を返す。"""
    marker = getfixturemarker(value)
    if marker is None:
        return f"fixture の {attribute} の印が読めない"
    if marker.params is not None or marker.autouse:
        return f"fixture の {attribute} が params / autouse を持つ"
    try:
        scope = fixture_scope(marker.scope)
    except MalformedCacheEntry:
        return f"fixture の {attribute} の scope が語でない(呼び出しで決まる scope)"
    function = inspect.unwrap(value)
    return FixtureRecord(
        attribute=attribute,
        name=value.name,
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
    """動的な記録だけを実値で補い、保存できない形は理由を返す。"""
    resolved: list[Record] = []
    problems: list[str] = []
    for record in records:
        if isinstance(record, Dynamic):
            function: object = vars(real_module).get(record.where)
            if not callable(function):
                problems.append(f"動的: {record.where} ({record.reason})")
                continue
            try:
                resolved.append(runtime_function_record(record.where, function))
            except UnrecordableError as exc:
                problems.append(f"動的: {record.where} ({exc})")
        else:
            resolved.append(record)
    return ResolvedRecords(resolved, problems)


def verify_records(real_module: types.ModuleType, name_matches: Callable[[str], bool]) -> Verified:
    """import後の記録がpytestの集める関数・印・fixtureを全部説明するか確かめる。"""
    try:
        records = decode_records(module_record_texts(real_module))
    except MalformedRecord as exc:
        return Verified([f"記録の形が違う: {exc}"], ())
    dynamic: bool = any(isinstance(r, Dynamic) for r in records)
    resolved = _resolve_dynamic_records(records, real_module)
    records, problems = resolved.records, resolved.problems
    functions = [r for r in records if isinstance(r, FunctionItem)]
    names = {r.name for r in functions}
    for record in functions:
        real = getattr(real_module, record.name, None)
        if real is None:
            problems.append(f"記録の {record.name} が module に無い")
            continue
        stub = _stub_function(record, Path(real_module.__file__ or ""), real_module.__name__)
        try:
            _check_marks(get_unpacked_marks(stub), get_unpacked_marks(real), record.name)
        except (RecordMismatch, UnrecordableError) as exc:
            problems.append(str(exc))
    module_marks = [r for r in records if isinstance(r, ModuleMarks)]
    recorded_marks = [name for r in module_marks for name in r.names]
    real_marks = [m.name for m in get_unpacked_marks(real_module)]
    if recorded_marks != real_marks:
        problems.append(f"module の印が記録と違う — 記録 {recorded_marks} / 実物 {real_marks}")
    fixtures: list[FixtureRecord] = []
    for name, value in vars(real_module).items():
        if name in names:
            continue
        if name in XUNIT_NAMES:
            problems.append(f"xunit の {name} がある")
        elif isinstance(value, FixtureFunctionDefinition):
            match _fixture_record(name, value):
                case FixtureRecord() as record:
                    fixtures.append(record)
                case reason:
                    problems.append(reason)
        elif getfixturemarker(value) is not None:
            problems.append(f"fixture の {name} の形が読めない")
        elif name_matches(name) and callable(value):
            problems.append(f"記録に無い test らしい名 {name} がある")
    return Verified(problems, tuple(fixtures), tuple(records), dynamic)


# ---------------------------------------------------------------------------
# 仮の module(記録から作る — 収集だけに使い、setup で実物に替わる)
# ---------------------------------------------------------------------------


def _param_value(value: ParamValue, position: int) -> object:
    """記録の params の値を、pytest が同じ id を付ける仮の値へ直す(literal は実物と同じ値そのもの)。"""
    match value:
        case LiteralValue(v):
            return v
        case OpaqueValue():
            return OpaqueParam(position)


def _stub_mark(decorator: Decorator) -> pytest.MarkDecorator:
    """記録の decorator 1 つを、実物と同じ名・同じ id を作る pytest の印へ直す。"""
    match decorator:
        case Parametrize(argnames, values, ids):
            return pytest.mark.parametrize(
                argnames, [_param_value(v, position) for position, v in enumerate(values)], ids=ids
            )
        case Mark(name):
            return getattr(pytest.mark, name)
        case SkipIf():
            # 条件は実行の時の式 — setup で実物の印に替わってから skipping の plugin が評価する。
            return pytest.mark.skipif(False, reason="(条件は import の後に実物の印で評価する)")


def _stub_function(item: FunctionItem, path: Path, module_name: str) -> Callable[..., None]:
    """記録の関数 1 つから、同じ名・同じ引数・同じ印の仮の関数を作る(pytest の fixture と parametrize の展開に渡す)。"""

    def stub(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"{module_name}.{item.name}: 記録から作った仮の関数が呼ばれた(setup で実物に替わるはず)")

    # 仮の関数の引数を pytest に見せる口(inspect.signature は __signature__ を読む)。
    stub.__signature__ = inspect.Signature([inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in item.argnames])
    stub.__name__ = stub.__qualname__ = item.name
    stub.__module__ = module_name
    # 報告の位置(pytest の reportinfo)は test file を指す。行は記録に無いので 1。
    stub.__code__ = stub.__code__.replace(co_filename=str(path), co_firstlineno=1, co_name=item.name)
    marked: Callable[..., None] = stub
    for decorator in reversed(item.decorators):
        marked = _stub_mark(decorator)(marked)
    return marked


def _stub_module_mark(name: str) -> pytest.MarkDecorator:
    """module の印の名から、収集の ``-m`` が同じに読む仮の印を作る(引数は setup で実物の印に替わる)。

    skipif / skip は引数が無いと無条件に飛ばす印と読まれるので、setup まで飛ばさない仮の条件を置く。
    """
    match name:
        case "skipif":
            return pytest.mark.skipif(False, reason="(条件は import の後に実物の印で評価する)")
        case _:
            return getattr(pytest.mark, name)


def _stub_fixture(record: FixtureRecord, module_name: str) -> FixtureFunctionDefinition:
    """記録の fixture 1 つから、同じ名・scope・引数の仮の fixture を作る。呼ばれた時は、setup で import 済みの本物の
    module の fixture の元の関数を呼ぶ(fixture の意味は本物の module にだけある)。"""

    def real_function() -> Callable[..., object]:
        """本物の module の fixture の元の関数(item の setup が先に本物の module を import している)。"""
        real_module = sys.modules.get(module_name)
        if real_module is None:
            raise RuntimeError(f"{module_name}: 仮の fixture {record.name} が、本物の module の import の前に呼ばれた")
        return inspect.unwrap(vars(real_module)[record.attribute])

    if record.generator:

        def generator_stub(**kwargs: object) -> Generator[object, None, None]:
            yield from cast(Generator[object, None, None], real_function()(**kwargs))

        stub: Callable[..., object] = generator_stub
    else:

        def value_stub(**kwargs: object) -> object:
            return real_function()(**kwargs)

        stub = value_stub
    # 仮の fixture の引数を pytest に見せる口(inspect.signature は __signature__ を読む)。
    stub.__signature__ = inspect.Signature([inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in record.argnames])
    stub.__name__ = stub.__qualname__ = record.attribute
    stub.__module__ = module_name
    return pytest.fixture(scope=record.scope, name=record.name)(stub)


def stub_module(
    records: tuple[Record, ...], fixtures: tuple[FixtureRecord, ...], path: Path, module_name: str
) -> types.ModuleType:
    """記録から仮の module を作る — pytest の Module の収集が読む物(関数・pytestmark・fixture)だけを持つ。"""
    module = types.ModuleType(module_name)
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
                raise AssertionError("Dynamic の記録を持つ file は plan_collection が import に回す")
    return module


# ---------------------------------------------------------------------------
# setup — 実物の module へ替え、記録と照合する
# ---------------------------------------------------------------------------


class RecordMismatch(Exception):
    """実物の module が記録と食い違った。"""


def _shape(marks: Iterable[PytestMark]) -> list[tuple[str, object]]:
    """印の並びを、記録と照合できる形(名と、parametrize なら引数の名と値の数・literal の値)にする。"""
    shape: list[tuple[str, object]] = []
    for mark in marks:
        match mark.name:
            case "parametrize":
                spec: Parametrize = parametrize_record(mark)
                # Pythonでは1 == True == 1.0だが、pytestのidと実値の型は異なる。
                literal_types = tuple(type(value.value) if isinstance(value, LiteralValue) else None
                                      for value in spec.values)
                shape.append(("parametrize", (spec, literal_types)))
            case "skipif":
                shape.append(("skipif", None))
            case name:
                shape.append((name, None))
    return shape


def _check_marks(stub_marks: list[PytestMark], real_marks: list[PytestMark], where: str) -> None:
    """仮の関数と実物の関数の印が、記録の言える範囲で同じかを確かめる。"""
    try:
        stub_shape = _shape(stub_marks)
        real_shape = _shape(real_marks)
    except UnrecordableError as exc:
        raise RecordMismatch(f"{where}: 実物の印を照合できない: {exc}") from exc
    if stub_shape != real_shape:
        raise RecordMismatch(f"{where}: 印が記録と違う — 記録 {stub_shape} / 実物 {real_shape}")


def import_module_for(path: Path, module_name: str) -> types.ModuleType:
    """test file の module を読む。親の package は通常の import で先に読み、test module はこの file から読んで親に付ける。

    親を読まずに ``sys.modules`` へ置くと、別の test が ``(import pkg.sub.mod :as …)`` で名を引いた時に落ちる
    (agora-redesign #1212)。test module そのものを file から読むのは、workspace の package ごとの ``tests`` のように
    同じ名の親が別の dir に在る時も、この file を読むため(その時は親に付けない)。
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
    """item の仮の関数を実物に替える: 関数・印・parametrize の値。食い違えば RecordMismatch。"""
    real = getattr(real_module, item.originalname, None)
    if real is None:
        raise RecordMismatch(f"{item.nodeid}: 実物の module に {item.originalname} が無い")
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
                callspec.params[argname] = actual.values[0] if isinstance(actual, ParameterSet) else actual
    item.obj = real
    callspec_marks = list(callspec.marks) if callspec is not None else []
    item.own_markers = [*real_marks, *callspec_marks]


def swap_in_real_module_marks(node: pytest.Module, stub_module_obj: types.ModuleType, real_module: types.ModuleType) -> None:
    """module の node の印(仮の module の pytestmark から付いた物)を実物の module の印に替える。名が記録と違えば RecordMismatch。"""
    stub_marks = get_unpacked_marks(stub_module_obj)
    real_marks = get_unpacked_marks(real_module)
    if [m.name for m in stub_marks] != [m.name for m in real_marks]:
        raise RecordMismatch(
            f"{node.nodeid}: module の印が記録と違う — 記録 {[m.name for m in stub_marks]} / 実物 {[m.name for m in real_marks]}"
        )
    others = [m for m in node.own_markers if not any(m is s for s in stub_marks)]
    node.own_markers = [*others, *real_marks]


def check_no_unrecorded_items(module_names: Iterable[str], real_module: types.ModuleType, name_matches: Callable[[str], bool], where: str) -> None:
    """実物の module が、記録に無い test らしい名を持たないかを確かめる(あれば、その item は仮の module に欠けていた)。"""
    recorded = set(module_names)
    extra = sorted(
        name for name, value in vars(real_module).items()
        if name_matches(name) and callable(value) and name not in recorded
        and getattr(value, "__module__", None) == real_module.__name__
    )
    if extra:
        raise RecordMismatch(f"{where}: 記録に無い test がある — {extra}")
