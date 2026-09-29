"""Hy の test file を import せずに収集し、item の setup で初めて import する(agora-redesign #1223・親 #1211)。

収集で test module を import すると module の最上位の実行が走り、1 file で 10 秒かかる(#1211 の実測)。そこで:

- **収集**: item を作る macro が展開の時に書いた記録(``doeff_hy.pytest_items``)を、Hy の importer が pyc と同じ条件で
  有効と判じた時だけ読み(``hy.importer.read_valid_records``)、記録と同じ引数・印・parametrize を持つ仮の関数を
  並べた仮の module を作る。その仮の module を pytest の Module の収集に渡すので、nodeid・parametrize の id・印は
  import して収集した時と同じ pytest の手順で作られる。
- **記録で説明できない file**(記録が無い / 古い / 動的な params / 記録に無い test らしい名・decorator の付いた定義・
  xunit の setup)は、今までどおり収集で import する。理由は収集の終わりに報告する。
- **setup**: item の fixture より先に、その file の module を通常の import の経路で 1 度だけ読み、item の関数・印・
  parametrize の値を実物に替える。実物が記録と食い違えば、その item を赤にし、記録を消す(黙って古い item で走らない)。
"""

from dataclasses import dataclass
import importlib
import importlib.util
import inspect
import sys
import types
from collections.abc import Callable, Iterable
from pathlib import Path
import pytest
from _pytest.mark.structures import Mark as PytestMark
from _pytest.mark.structures import get_unpacked_marks
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
    RecordedModule,
    SkipIf,
    read_module,
)
from hy.importer import HyLoader, read_valid_records

# module の最上位で呼ぶと module ごと飛ばす pytest の関数(from pytest import skip の形も同じ名)。
MODULE_SKIPS = frozenset({"skip", "importorskip"})

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

    recorded: RecordedModule


@dataclass(frozen=True)
class NeedsImport:
    """記録で説明できない file — 収集で import する。``reason`` は収集の終わりに報告する。"""

    reason: str


CollectionPlan = Indexed | NeedsImport


def plan_collection(path: Path, name_matches: Callable[[str], bool]) -> CollectionPlan:
    """収集で import せずに済むかを、記録だけから決める。

    ``name_matches`` は pytest の ``python_functions`` / ``python_classes`` の照合(その名を pytest が test として
    集めるか)。記録に無い名を pytest が集める file は、仮の module では item が欠けるので import する。
    """
    records = read_valid_records(str(path))
    if records is None:
        return NeedsImport("記録なし(pyc が無い・古い)")
    try:
        recorded = read_module(records)
    except MalformedRecord as exc:
        return NeedsImport(f"記録の形が違う: {exc}")
    # module ごと飛ばす呼び出し(pytest.skip / importorskip)は、import すれば item が 0 本になる。
    skips = sorted(c for c in recorded.top_level_calls if c.rpartition(".")[2] in MODULE_SKIPS)
    if skips:
        return NeedsImport("最上位で呼ぶ: " + ", ".join(skips))
    dynamic = [r for r in recorded.records if isinstance(r, Dynamic)]
    if dynamic:
        return NeedsImport("動的: " + ", ".join(f"{d.where} ({d.reason})" for d in dynamic))
    functions = {r.name for r in recorded.records if isinstance(r, FunctionItem)}
    has_module_marks = any(isinstance(r, ModuleMarks) for r in recorded.records)
    unaccounted = sorted(
        name
        for name in recorded.bound_names
        if name not in functions
        and (
            name_matches(name)
            or name in XUNIT_NAMES
            or (name == "pytestmark" and not has_module_marks)
        )
    )
    # decorator が名に依らず pytest に意味を持たせる定義(fixture)は、仮の module に無いので import する。
    unaccounted += sorted(
        name
        for name, heads in recorded.decorators.items()
        if name not in functions and any(head.rpartition(".")[2] == "fixture" for head in heads)
    )
    if unaccounted:
        return NeedsImport("記録に無い名: " + ", ".join(unaccounted))
    return Indexed(recorded)


# ---------------------------------------------------------------------------
# 仮の module(記録から作る — 収集だけに使い、setup で実物に替わる)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class OpaqueParam:
    """中身を問わない params の値の仮の値。pytest の id は実物と同じく ``<引数の名><番号>`` になる。

    ``position`` は parametrize の値の並びの中の位置 — setup でこの位置の実物の値に替える
    (pytest の ``callspec.indices`` は後で通し番号に振り直されるので使えない)。
    """

    position: int

    def __repr__(self) -> str:
        return f"<import の後に実物の値へ替わる #{self.position}>"


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
        case Parametrize(argnames, values):
            return pytest.mark.parametrize(
                argnames, [_param_value(v, position) for position, v in enumerate(values)]
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
    setattr(
        stub,
        "__signature__",
        inspect.Signature(
            [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in item.argnames]
        ),
    )
    stub.__name__ = stub.__qualname__ = item.name
    stub.__module__ = module_name
    # 報告の位置(pytest の reportinfo)は test file を指す。行は記録に無いので 1。
    stub.__code__ = stub.__code__.replace(co_filename=str(path), co_firstlineno=1, co_name=item.name)
    marked: Callable[..., None] = stub
    for decorator in reversed(item.decorators):
        marked = _stub_mark(decorator)(marked)
    return marked


def stub_module(recorded: RecordedModule, path: Path, module_name: str) -> types.ModuleType:
    """記録から仮の module を作る — pytest の Module の収集が読む物(関数と pytestmark)だけを持つ。"""
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    for record in recorded.records:
        match record:
            case FunctionItem():
                setattr(module, record.name, _stub_function(record, path, module_name))
            case ModuleMarks(names):
                setattr(module, "pytestmark", [getattr(pytest.mark, name) for name in names])
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
                argnames, values = mark.args[0], list(mark.args[1])
                literal = [
                    v if isinstance(v, (str, int, float, bool, type(None))) else OpaqueParam
                    for v in values
                ]
                shape.append(("parametrize", (argnames, literal)))
            case "skipif":
                shape.append(("skipif", None))
            case name:
                shape.append((name, None))
    return shape


def _check_marks(stub_marks: list[PytestMark], real_marks: list[PytestMark], where: str) -> None:
    """仮の関数と実物の関数の印が、記録の言える範囲で同じかを確かめる。"""
    stub_shape = [
        (name, (arg[0], [OpaqueParam if isinstance(v, OpaqueParam) else v for v in arg[1]]))
        if name == "parametrize" and isinstance(arg, tuple)
        else (name, arg)
        for name, arg in _shape(stub_marks)
    ]
    real_shape = _shape(real_marks)
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
                callspec.params[argname] = real_values[argname][value.position]
    item.obj = real
    callspec_marks = list(callspec.marks) if callspec is not None else []
    item.own_markers = [*real_marks, *callspec_marks]


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


def forget_records(path: Path) -> None:
    """記録が実物と食い違った file の pyc を消す — 次の収集はその file を import し、展開をやり直して記録を書き直す。"""
    Path(importlib.util.cache_from_source(str(path))).unlink(missing_ok=True)
