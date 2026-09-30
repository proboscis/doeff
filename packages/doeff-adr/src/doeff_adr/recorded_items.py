"""記録から集める file の item の生成 — pytest の内部に触るのはこの module だけ(agora-redesign #1551)。

pytest の Module の収集は、module の属性を全部なめて属性ごとに ``pytest_pycollect_makeitem`` を呼び、test 関数ごとに
FunctionDefinition(fixture の閉包を解く)→ Metafunc → ``pytest_generate_tests`` → Function の順で item を作る。記録から集める
file は pytest に見せる関数の名が記録で分かっているので、次の 3 つを変える:

- 属性の走査を、記録の名の並びに替える。hook は名ごとに今までどおり呼ぶので、他の plugin の wrapper と実装はそのまま効く。
- fixture の閉包を、同じ引数・同じ直接 parametrize の関数のあいだで共有する。閉包は「module の node から見える fixture」と
  「初期の引数」だけで決まるので、同じ module の兄弟の関数なら同じ。共有する list / dict は関数ごとに写す(parametrize が書き換える)。
- ``pytest_generate_tests`` の実装が pytest 本体の 2 つ(python の parametrize の印・fixtures の params)だけの時、その 2 つが何もしないと
  分かる関数(印に parametrize が無く・閉包の fixture に params が無い)は、FunctionDefinition と Metafunc を作らずに Function を 1 つ
  直に作る。他の実装(plugin・conftest)が 1 つでもあれば中身は知らないので、全部の関数を pytest と同じ手順で作る(その実装が
  FunctionDefinition の型を求めても通る)。

ここで触る pytest の内部: ``Session._fixturemanager``・``PyCollector._genfunctions``(collector が上書きして委ねる口)・
``FunctionDefinition``・``Function._fixtureinfo``・``Metafunc(_ispytest=True)``・``Metafunc._calls`` /
``_recompute_direct_params_indices``・``CallSpec2._idlist``。pytest 9.1 系の呼び方なので、他の版では記録からの生成をせず
仮の module を pytest の Module の収集にそのまま渡す(``supported_pytest``)。
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

import _pytest.python
import pytest
from _pytest.fixtures import FixtureDef, FixtureManager, FuncFixtureInfo
from _pytest.python import FunctionDefinition, Metafunc
from doeff_hy.pytest_items import FunctionItem, Mark, Parametrize

from doeff_adr.lazy_collection import RecordedModule

# この module が書かれた pytest の版(major, minor)。
SUPPORTED_PYTEST: tuple[int, int] = (9, 1)


def supported_pytest() -> bool:
    """走っている pytest が、この module の内部の呼び方の版か。"""
    return tuple(pytest.version_tuple[:2]) == SUPPORTED_PYTEST


# ---------------------------------------------------------------------------
# pytest_generate_tests の実装の顔ぶれ(module の収集ごとに読む — conftest は収集の途中で登録される)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoreOnly:
    """pytest 本体の 2 つの実装だけ。"""


@dataclass(frozen=True)
class Foreign:
    """pytest 本体の外の実装がある(plugin・conftest)— 名は報告に載せる。"""

    names: tuple[str, ...]


GenerateTestsImpls = CoreOnly | Foreign


def generate_tests_impls(config: pytest.Config) -> GenerateTestsImpls:
    """今登録されている pytest_generate_tests の実装のうち、pytest 本体(python の module と FixtureManager)の外の物の名。"""
    names: list[str] = []
    for impl in config.hook.pytest_generate_tests.get_hookimpls():
        plugin: object = impl.plugin
        if plugin is _pytest.python or isinstance(plugin, FixtureManager):
            continue
        match config.pluginmanager.get_name(plugin):
            case str() as name:
                names.append(name)
            case None:
                names.append(type(plugin).__name__)
    return CoreOnly() if not names else Foreign(tuple(names))


# ---------------------------------------------------------------------------
# fixture の閉包の共有
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureKey:
    """閉包を共有できる関数の組の鍵: 引数の名と、直接 parametrize する引数(閉包から除く物)。"""

    argnames: tuple[str, ...]
    ignore_args: frozenset[str]


@dataclass(frozen=True)
class FixtureClosure:
    """pytest が解いた fixture の閉包の写し(parametrize が書き換える前の形)。関数ごとに ``fresh`` で新しい list / dict を渡す。"""

    argnames: tuple[str, ...]
    initialnames: tuple[str, ...]
    names_closure: tuple[str, ...]
    name2fixturedefs: tuple[tuple[str, Sequence[FixtureDef[object]]], ...]
    # 閉包のどれかの fixture が params を持つ(fixtures の pytest_generate_tests が parametrize する)。
    parametrizes: bool

    def fresh(self) -> FuncFixtureInfo:
        """この閉包を持つ新しい FuncFixtureInfo(list と dict は関数ごとに別の物)。"""
        return FuncFixtureInfo(
            self.argnames,
            self.initialnames,
            list(self.names_closure),
            dict(self.name2fixturedefs),
        )


def _snapshot(info: FuncFixtureInfo) -> FixtureClosure:
    """pytest が node のために解いた閉包を、共有できる写しにする。"""
    return FixtureClosure(
        argnames=tuple(info.argnames),
        initialnames=tuple(info.initialnames),
        names_closure=tuple(info.names_closure),
        name2fixturedefs=tuple((name, defs) for name, defs in info.name2fixturedefs.items()),
        parametrizes=any(fixturedef.params is not None for defs in info.name2fixturedefs.values() for fixturedef in defs),
    )


@dataclass
class RecordedCollection:
    """記録から集める file 1 つの、item の生成の状態(collector 1 つにつき 1 つ — session をまたいで持たない)。"""

    recorded: RecordedModule
    _mut_closures: dict[FixtureKey, FixtureClosure] = field(default_factory=dict)


@dataclass
class GenerationTally:
    """session 1 回の、記録からの item の生成の数え上げ(収集の終わりの報告のため)。"""

    _mut_direct: int = 0
    _mut_standard: int = 0
    _mut_foreign: dict[str, None] = field(default_factory=dict)

    def report(self) -> str:
        """収集の終わりの 1 行: 直に作った関数の数・pytest の手順で作った関数の数・他の pytest_generate_tests の実装の名。"""
        line = f"doeff-adr: 記録からの item の生成 — 直に {self._mut_direct} 関数・pytest の手順で {self._mut_standard} 関数"
        if self._mut_foreign:
            line += f"(pytest_generate_tests の他の実装: {'・'.join(self._mut_foreign)})"
        return line


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def collect_recorded(collector: pytest.Module, recorded: RecordedModule) -> list[pytest.Item | pytest.Collector]:
    """記録の名の並びで item を作る — pytest の Module.collect の、属性の走査を記録に替えた形。

    xunit の関数(setup_module 等)は記録の file には無い(あれば import で集める)ので、その登録は省く。module の fixture
    (仮の fixture)の登録は pytest の Module.collect と同じ口(FixtureManager.parsefactories)で行う。
    """
    collector.session._fixturemanager.parsefactories(collector)
    ihook = collector.ihook
    module = collector.obj
    items: list[pytest.Item | pytest.Collector] = []
    for name in recorded.functions:
        result: object = ihook.pytest_pycollect_makeitem(collector=collector, name=name, obj=getattr(module, name))
        match result:
            case None:
                continue
            case list():
                items.extend(result)
            case pytest.Item() | pytest.Collector():
                items.append(result)
            case _:
                raise TypeError(f"pytest_pycollect_makeitem が item でも collector でも無い物を返した: {result!r}")
    return items


def _mark_names(record: FunctionItem) -> frozenset[str]:
    """関数の記録の、引数の無い印の名。"""
    return frozenset(decorator.name for decorator in record.decorators if isinstance(decorator, Mark))


def generate_functions(
    collector: pytest.Module,
    state: RecordedCollection,
    name: str,
    stub: object,
    impls: GenerateTestsImpls,
    tally: GenerationTally,
) -> Iterator[pytest.Function]:
    """記録の関数 1 つの item を作る(pytest の PyCollector._genfunctions の、閉包を共有し・何もしない手順を省いた形)。"""
    record = state.recorded.functions[name]
    direct_args = frozenset(decorator.argnames for decorator in record.decorators if isinstance(decorator, Parametrize))
    key = FixtureKey(record.argnames, direct_args)
    # usefixtures は印の引数で閉包が変わる(記録には引数が無い)ので、その関数は共有しない。
    sharable = "usefixtures" not in _mark_names(record) and "usefixtures" not in state.recorded.module_marks
    # pytest 本体の 2 つの実装のうち python の物は parametrize の印(関数と module)にだけ反応する。
    static = isinstance(impls, CoreOnly) and not direct_args and "parametrize" not in state.recorded.module_marks
    if isinstance(impls, Foreign):
        for foreign in impls.names:
            tally._mut_foreign.setdefault(foreign, None)
    closure = state._mut_closures.get(key) if sharable else None
    if closure is None:
        # 最初の 1 本は pytest に閉包を解かせる(この node のために解いた物を写して共有する)。
        probe = pytest.Function.from_parent(collector, name=name)
        closure = _snapshot(probe._fixtureinfo)
        if sharable:
            state._mut_closures[key] = closure
        if static and not closure.parametrizes:
            tally._mut_direct += 1
            yield probe
            return
    elif static and not closure.parametrizes:
        tally._mut_direct += 1
        yield pytest.Function.from_parent(collector, name=name, fixtureinfo=closure.fresh())
        return
    tally._mut_standard += 1
    definition = FunctionDefinition.from_parent(collector, name=name, callobj=stub, fixtureinfo=closure.fresh())
    yield from _through_pytest_generate_tests(collector, definition, name)


def _through_pytest_generate_tests(
    collector: pytest.Module, definition: FunctionDefinition, name: str
) -> Iterator[pytest.Function]:
    """pytest の _genfunctions の後半と同じ手順: Metafunc → pytest_generate_tests → callspec ごとの Function。

    module の直下の pytest_generate_tests は、記録の file には無い(XUNIT_NAMES — あれば import で集める)。
    """
    fixtureinfo = definition._fixtureinfo
    metafunc = Metafunc(
        definition=definition,
        fixtureinfo=fixtureinfo,
        config=collector.config,
        cls=None,
        module=collector.obj,
        _ispytest=True,
    )
    collector.ihook.pytest_generate_tests.call_extra([], {"metafunc": metafunc})
    if not metafunc._calls:
        yield pytest.Function.from_parent(collector, name=name, fixtureinfo=fixtureinfo)
        return
    metafunc._recompute_direct_params_indices()
    # 直接 parametrize が閉包の fixture を隠すことがあるので、閉包を作り直す(pytest と同じ)。
    fixtureinfo.prune_dependency_tree()
    for callspec in metafunc._calls:
        subname = f"{name}[{callspec.id}]" if callspec._idlist else name
        yield pytest.Function.from_parent(
            collector,
            name=subname,
            callspec=callspec,
            fixtureinfo=fixtureinfo,
            keywords={callspec.id: True},
            originalname=name,
        )
