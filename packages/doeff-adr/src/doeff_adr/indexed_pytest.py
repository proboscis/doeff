"""記録からの収集専用の pytest 内部境界 (#1551)。

型付きの session 内キャッシュと item 生成をここだけで所有する。pytest の class や関数を
書き換えない。未知の収集 hook は最適化前に検知して通常の import と収集へ戻す。
"""

import sys
import types
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

import pytest
from _pytest import python, runner, unittest
from _pytest.capture import CaptureManager
from _pytest.fixtures import FixtureDef, FixtureManager, FuncFixtureInfo
from _pytest.mark.structures import get_unpacked_marks
from _pytest.nodes import Node
from _pytest.python import Function, FunctionDefinition, Metafunc
from doeff_hy.pytest_items import FunctionItem, Parametrize

from doeff_adr.item_cache import FixtureRecord


# 名前だけで信用せず、既知の実装そのものと照合する。任意 plugin の wrapper も通常経路へ戻す。
def supports_indexed_collection(collector: pytest.Module) -> bool:
    manager = collector.session._fixturemanager
    makeitem: list[object] = [python.pytest_pycollect_makeitem, unittest.pytest_pycollect_makeitem]
    generate: list[object] = [python.pytest_generate_tests, manager.pytest_generate_tests]
    for module_name, makeitem_name, generate_name in (
        (
            "pytest_asyncio.plugin",
            "pytest_pycollect_makeitem_convert_async_functions_to_subclass",
            "pytest_generate_tests",
        ),
        ("anyio.pytest_plugin", "pytest_pycollect_makeitem", None),
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            makeitem.append(vars(module).get(makeitem_name))
            if generate_name is not None:
                generate.append(vars(module).get(generate_name))
    hook = collector.ihook
    reports: list[object] = [
        runner.pytest_make_collect_report,
        CaptureManager.pytest_make_collect_report,
    ]
    # pytest の版によって fixture manager の report wrapper は有無が異なる。
    reports.append(vars(FixtureManager).get("pytest_make_collect_report"))
    return (
        all(impl.function in makeitem for impl in hook.pytest_pycollect_makeitem.get_hookimpls())
        and all(impl.function in generate for impl in hook.pytest_generate_tests.get_hookimpls())
        and not hook.pytest_make_parametrize_id.get_hookimpls()
        and all(
            impl.function == collector.session.pytest_collectstart
            for impl in hook.pytest_collectstart.get_hookimpls()
        )
        and all(
            (
                impl.function.__func__
                if isinstance(impl.function, types.MethodType)
                else impl.function
            )
            in reports
            for impl in hook.pytest_make_collect_report.get_hookimpls()
        )
    )


@dataclass(frozen=True)
class FixtureKey:
    visibility: Node | None
    argnames: tuple[str, ...]
    usefixtures: tuple[object, ...]
    direct_args: frozenset[str]


@dataclass
class FixtureInfoCache:
    """1 session の登録内容ごとに解決を共有する。各 item へは list/dict のコピーを返す。

    各 module の収集の直前に登録簿を比較するので、下位 conftest と追加登録を見落とさない。
    内部の fixture 配列がその場で伸びた場合も tuple の snapshot は独立している。
    """

    _mut_definitions: dict[str, tuple[FixtureDef[object], ...]] = field(default_factory=dict)
    _mut_entries: dict[FixtureKey, FuncFixtureInfo] = field(default_factory=dict)

    def refresh(self, manager: FixtureManager) -> None:
        definitions = {name: tuple(defs) for name, defs in manager._arg2fixturedefs.items()}
        if definitions != self._mut_definitions:
            self._mut_definitions = definitions
            self._mut_entries.clear()

    def get(
        self, key: FixtureKey, manager: FixtureManager, collector: pytest.Module
    ) -> FuncFixtureInfo | None:
        info = self._mut_entries.get(key)
        if info is None:
            return None
        # 同じ親 dir でも、特定の module/item だけを対象とする登録は兄弟と見え方が違う。
        # 解決済みだけでなく closure の欠けている名も照合する (C の lookup の考え方)。
        if any(
            tuple(manager.getfixturedefs(name, collector) or ())
            != info.name2fixturedefs.get(name, ())
            for name in info.names_closure
        ):
            return None
        return _copy_info(info)

    def remember(self, key: FixtureKey, info: FuncFixtureInfo) -> None:
        self._mut_entries[key] = _copy_info(info)


def _copy_info(info: FuncFixtureInfo) -> FuncFixtureInfo:
    return FuncFixtureInfo(
        info.argnames,
        info.initialnames,
        list(info.names_closure),
        {name: tuple(defs) for name, defs in info.name2fixturedefs.items()},
    )


_FIXTURE_INFO_KEY = pytest.StashKey[FixtureInfoCache]()


class IndexedFunctions:
    """1 module の記録に限定した item 生成。plugin の呼び出し自体は通常の hook を通す。"""

    def __init__(
        self,
        collector: pytest.Module,
        records: Sequence[FunctionItem],
        fixtures: Sequence[FixtureRecord],
    ) -> None:
        self.collector = collector
        self.records = {record.name: record for record in records}
        manager = collector.session._fixturemanager
        if fixtures:
            manager.parsefactories(collector)
        self.cache = collector.config.stash.setdefault(_FIXTURE_INFO_KEY, FixtureInfoCache())
        self.cache.refresh(manager)
        self.visibility = collector if fixtures else collector.parent

    def collect(self) -> list[pytest.Item | pytest.Collector]:
        return [item for name in self.records for item in self._collect_name(name)]

    def _collect_name(self, name: str) -> Iterator[pytest.Item | pytest.Collector]:
        collector = self.collector
        found = collector.ihook.pytest_pycollect_makeitem(
            collector=collector,
            name=name,
            obj=vars(collector.obj)[name],
        )
        if isinstance(found, list):
            yield from found
        elif found is not None:
            yield found

    def generate(self, name: str, function: Callable[..., object]) -> Iterator[Function]:
        collector = self.collector
        record = self.records[name]
        marks = [*get_unpacked_marks(function), *collector.iter_markers()]
        key = FixtureKey(
            self.visibility,
            record.argnames,
            tuple(arg for mark in marks if mark.name == "usefixtures" for arg in mark.args),
            frozenset(
                decorator.argnames
                for decorator in record.decorators
                if isinstance(decorator, Parametrize)
            ),
        )
        # 引数なし usefixtures は fixture 解決のたびに警告を出す。共有して警告を省かない。
        shareable = not any(mark.name == "usefixtures" and not mark.args for mark in marks)
        cached = (
            self.cache.get(key, collector.session._fixturemanager, collector) if shareable else None
        )
        if key.direct_args:
            definition = FunctionDefinition.from_parent(
                collector, name=name, callobj=function, fixtureinfo=cached
            )
            info = definition._fixtureinfo
        else:
            item = Function.from_parent(collector, name=name, callobj=function, fixtureinfo=cached)
            info = item._fixtureinfo
            if cached is None and shareable:
                self.cache.remember(key, info)
            # 既知の hook だけ: sync 関数で印も fixture の params も無ければ、生成 hook は全て無操作。
            if not any(
                fixture.params is not None
                for definitions in info.name2fixturedefs.values()
                for fixture in definitions
            ):
                yield item
                return
            definition = FunctionDefinition.from_parent(
                collector, name=name, callobj=function, fixtureinfo=info
            )
        if cached is None and shareable:
            self.cache.remember(key, info)
        metafunc = Metafunc(
            definition=definition,
            fixtureinfo=info,
            config=collector.config,
            cls=None,
            module=collector.obj,
            _ispytest=True,
        )
        collector.ihook.pytest_generate_tests.call_extra([], {"metafunc": metafunc})
        if not metafunc._calls:
            yield Function.from_parent(
                collector, name=name, callobj=function, fixtureinfo=_copy_info(info)
            )
            return
        metafunc._recompute_direct_params_indices()
        info.prune_dependency_tree()
        for callspec in metafunc._calls:
            subname = f"{name}[{callspec.id}]" if callspec._idlist else name
            yield Function.from_parent(
                collector,
                name=subname,
                callspec=callspec,
                fixtureinfo=_copy_info(info),
                keywords={callspec.id: True},
                originalname=name,
            )
