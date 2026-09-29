"""記録から作った仮の module の収集を、pytest の通常の経路と同じ item のまま軽くする(agora-redesign #1551・親 #1460)。

pytest の非公開の部品(``FixtureManager`` の登録簿・``FuncFixtureInfo``・``Metafunc`` の結果・hook の実装の持ち主)に
触るのは doeff-adr の中でこの module だけ。pytest を上げてこれらの形が変われば、ここの型の検査とテスト
(tests/test_recorded_collection.py)が赤になる。

記録で説明できる file の仮の module(``lazy_collection.stub_module``)は、記録の関数・記録の fixture・``pytestmark`` だけを
持つ。だから pytest の ``Module.collect`` が汎用にしていることの答えは、記録から先に決まっている:

- item になる名 = 記録の関数の名。同じ名の記録が 2 つあれば、Python の module と同じく最初の位置に最後の値が残る
  (仮の module は記録の順に ``setattr`` で作るので、その ``__dict__`` の順がそのまま答え)。
- xunit の関数(``setup_module`` 等)は無い — ``verify_records`` が xunit の名を持つ file を保存しない。
- 同じ module の中で、引数の名と、fixture の解決に効く印(直の parametrize の引数の名)が同じ関数は、pytest の
  fixture の解決(``FixtureManager.getfixtureinfo`` の結果)も同じになる。解決は親の鎖(module・dir・session)の
  fixture・autouse・印と、関数の引数と印だけから決まり、同じ module の兄弟は親の鎖が同じだから。

この 3 つを使い、汎用の属性の走査・xunit の探索・同じ形の関数ごとの fixture の解決の繰り返しを省く。item の生成
(``pytest_pycollect_makeitem``)・parametrize の展開(``pytest_generate_tests`` と ``Metafunc``)は pytest の hook の
ままで、hook を飛ばさない。

通常の経路と結果が変わりうるのは、pytest と下の既知の plugin 以外の実装がこの 2 つの hook に加わる時(未知の
plugin は、属性の名で item を作るかもしれず、収集の途中で fixture を登録するかもしれない)。その時は ``known_collection_hooks`` が None を返し、呼ぶ側は module の
収集を丸ごと通常の経路へ戻す。
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from types import ModuleType

import pytest
from _pytest.fixtures import FuncFixtureInfo
from _pytest.python import FunctionDefinition, Metafunc
from doeff_hy.pytest_items import FunctionItem, Mark, Parametrize, Record, SkipIf
from pluggy import HookCaller

# pytest_pycollect_makeitem の実装のうち、中身を読んで確かめた物の module(pytest 9.1 / pytest-asyncio 1.4 / anyio 4.15)。
# 記録の関数の名だけを渡しても通常の経路と同じ item になる — 関数でない名(fixture・pytestmark)からは item を作らない:
#   _pytest.python   = 既定の実装(関数なら _genfunctions、class なら Class)
#   _pytest.unittest = unittest.TestCase の子の class だけを集める
#   anyio.pytest_plugin   = coroutine の関数だけに印を付ける(仮の関数は同期の def)
#   pytest_asyncio.plugin = 包み(wrapper)— coroutine の Function だけを別の item の型に替える
_KNOWN_MAKEITEM_OWNERS = frozenset(
    {"_pytest.python", "_pytest.unittest", "anyio.pytest_plugin", "pytest_asyncio.plugin"}
)
# pytest_generate_tests の実装のうち、中身を読んで確かめた物の module。どれも収集の途中で fixture を登録しない:
#   _pytest.python   = 関数の parametrize の印を Metafunc.parametrize へ渡す
#   _pytest.fixtures = params を持つ fixture を間接の parametrize にする(FixtureManager の method)
#   pytest_asyncio.plugin = definition の関数が coroutine でなければ何もしない(仮の関数は同期の def)
_KNOWN_GENERATE_OWNERS = frozenset({"_pytest.python", "_pytest.fixtures", "pytest_asyncio.plugin"})


@dataclass(frozen=True)
class KnownCollectionHooks:
    """この module の位置で効く item の生成の 2 つの hook の呼び口 — 全部の実装が既知の物だと確かめた後の値。

    ``collect`` の間は同じ呼び口を使う(``collector.ihook`` は引くたびに conftest の絞り込みを作り直すので、
    関数ごとに引くと 1 回の収集で 1 万回近くになる)。
    """

    makeitem: HookCaller
    generate_tests: HookCaller


def known_collection_hooks(collector: pytest.Module) -> KnownCollectionHooks | None:
    """この module の収集に加わる item の生成の hook が全部既知の実装なら、その呼び口(未知の実装があれば None —
    呼ぶ側は通常の経路で収集する)。

    ``collector.ihook`` はこの file の位置で効く conftest の実装も含む(conftest の hook も未知の実装に数える)。
    """
    hooks = collector.ihook
    makeitem = hooks.pytest_pycollect_makeitem
    generate_tests = hooks.pytest_generate_tests
    known = all(impl.function.__module__ in _KNOWN_MAKEITEM_OWNERS for impl in makeitem.get_hookimpls()) and all(
        impl.function.__module__ in _KNOWN_GENERATE_OWNERS for impl in generate_tests.get_hookimpls()
    )
    return KnownCollectionHooks(makeitem, generate_tests) if known else None


@dataclass(frozen=True)
class FixtureShape:
    """同じ module の中で fixture の解決を同じにする関数の形 — 引数の名と、直の parametrize の引数の名の並び。

    仮の関数の印は記録から作るので、parametrize はいつも直(indirect なし)。``usefixtures`` の印は解決を変え、
    引数の無い ``usefixtures`` は item ごとに警告を出すので、その印を持つ関数は共有しない(``shareable`` が偽)。
    """

    argnames: tuple[str, ...]
    direct_parametrize: tuple[str, ...]
    shareable: bool


def fixture_shape(record: FunctionItem) -> FixtureShape:
    """記録の関数 1 つの fixture の形。"""
    direct: list[str] = []
    shareable = True
    for decorator in record.decorators:
        match decorator:
            case Parametrize(argnames):
                direct.append(argnames)
            case Mark(name):
                shareable = shareable and name != "usefixtures"
            case SkipIf():
                pass
    return FixtureShape(record.argnames, tuple(direct), shareable)


@dataclass
class SharedFixtureResolutions:
    """1 つの仮の module の中で、同じ fixture の形の関数に fixture の解決を共有する。

    ``shapes`` は名から fixture の形(同じ名の記録が 2 つあれば最後の物 — 仮の module に残る値と同じ)。
    ``_mut_resolved`` は形ごとの解決の結果(item に渡す前に毎回写す — parametrize は item ごとの写しを書き換える)。
    """

    shapes: dict[str, FixtureShape]
    _mut_resolved: dict[FixtureShape, FuncFixtureInfo] = field(default_factory=dict)

    def resolved_for(self, name: str) -> FuncFixtureInfo | None:
        """この名の関数に使える、先に解決した fixture の写し(無ければ None — pytest に解決させる)。"""
        shape = self.shapes[name]
        resolved = self._mut_resolved.get(shape)
        return None if resolved is None else _copy_fixture_info(resolved)

    def remember(self, collector: pytest.Module, name: str, info: FuncFixtureInfo) -> None:
        """pytest が解決した直後(parametrize が書き換える前)の結果を、同じ形の次の関数のために写して取っておく。"""
        shape = self.shapes[name]
        if shape.shareable and shape not in self._mut_resolved and _resolution_is_module_wide(collector, info):
            self._mut_resolved[shape] = _copy_fixture_info(info)


@dataclass(frozen=True)
class RecordedModuleCollection:
    """記録から作った仮の module 1 つの、1 回の ``collect`` の間だけの状態 — fixture の解決の共有と、既知と確かめた
    hook の呼び口。``collect`` が終われば捨てる — 次の収集(同じ process の再収集を含む)は空から始める。"""

    resolutions: SharedFixtureResolutions
    hooks: KnownCollectionHooks

    @classmethod
    def from_records(cls, records: Sequence[Record], hooks: KnownCollectionHooks) -> "RecordedModuleCollection":
        shapes = {record.name: fixture_shape(record) for record in records if isinstance(record, FunctionItem)}
        return cls(SharedFixtureResolutions(shapes), hooks)


def _copy_fixture_info(info: FuncFixtureInfo) -> FuncFixtureInfo:
    """item ごとの写し — ``Metafunc.parametrize`` は ``name2fixturedefs`` を、``prune_dependency_tree`` は
    ``names_closure`` をその場で書き換えるので、共有の元へ漏らさない(中の fixture の並びは tuple で書き換わらない)。"""
    return FuncFixtureInfo(
        argnames=info.argnames,
        initialnames=info.initialnames,
        names_closure=list(info.names_closure),
        name2fixturedefs=dict(info.name2fixturedefs),
    )


def _resolution_is_module_wide(collector: pytest.Module, info: FuncFixtureInfo) -> bool:
    """この解決が、同じ module の兄弟の関数にもそのまま当たるか。

    兄弟で違いうるのは、item(または class)の nodeid を名指しした登録だけ — 非推奨の nodeid の文字列での
    fixture の登録(``baseid`` に ``::`` を含む)と autouse の登録。解決に出てくる名の登録を全部見て、1 つでも
    あれば共有しない。この module の ``collect`` の間は既知の hook しか走らない(``known_collection_hooks``)
    ので、見た後に登録が増えることはない。
    """
    fixture_manager = collector.session._fixturemanager
    item_prefix = collector.nodeid + "::"
    if any(nodeid.startswith(item_prefix) for nodeid in fixture_manager._nodeid_autousenames):
        return False
    return not any(
        "::" in definition.baseid
        for name in info.names_closure
        for definition in fixture_manager._arg2fixturedefs.get(name, ())
    )


def collect_recorded(
    collector: pytest.Module, module: ModuleType, collection: RecordedModuleCollection
) -> list[pytest.Item | pytest.Collector]:
    """仮の module の item を、記録の関数の名についてだけ ``pytest_pycollect_makeitem`` を呼んで作る。

    ``Module.collect`` と同じく、先に module の fixture を登録する。xunit の fixture の登録と ``__test__`` の確かめは
    省く(仮の module はどちらの名も持たない — ``stub_module`` が作らない)。
    """
    collector.session._fixturemanager.parsefactories(collector)
    collected: list[pytest.Item | pytest.Collector] = []
    for name, obj in vars(module).items():
        if name not in collection.resolutions.shapes:
            continue
        made = collection.hooks.makeitem(collector=collector, name=name, obj=obj)
        match made:
            case None:
                pass
            case list():
                collected.extend(made)
            case _:
                collected.append(made)
    return collected


def generate_functions(
    collector: pytest.Module, name: str, function: object, collection: RecordedModuleCollection
) -> Iterator[pytest.Function]:
    """``PyCollector._genfunctions`` と同じ手順で、関数 1 つの item を作る。``pytest_generate_tests`` は全部の実装を呼び、
    parametrize は ``Metafunc`` が展開する。仮の module は ``pytest_generate_tests`` を持たない(``stub_module``)。

    通常の経路との違いは、fixture の解決を同じ形の兄弟と共有すること(解決済みの写しを ``FunctionDefinition`` に
    渡す)だけ。parametrize の無い関数でも、hook に見せる ``FunctionDefinition`` と item の ``Function`` を通常の経路と
    同じく別に作る — 1 つにまとめるには ``Metafunc`` の型(``FunctionDefinition``)に ``Function`` を渡す型逃げが要る。
    """
    definition = FunctionDefinition.from_parent(
        collector, name=name, callobj=function, fixtureinfo=collection.resolutions.resolved_for(name)
    )
    fixtureinfo = definition._fixtureinfo
    collection.resolutions.remember(collector, name, fixtureinfo)
    metafunc = Metafunc(
        definition=definition,
        fixtureinfo=fixtureinfo,
        config=collector.config,
        cls=None,
        module=collector.obj,
        _ispytest=True,
    )
    collection.hooks.generate_tests.call_extra([], {"metafunc": metafunc})
    if not metafunc._calls:
        yield pytest.Function.from_parent(collector, name=name, fixtureinfo=fixtureinfo)
        return
    metafunc._recompute_direct_params_indices()
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
