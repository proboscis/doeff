"""Internal pytest boundary dedicated to collection from records (#1551).

Typed per-session caching and item generation are owned only here. pytest classes and functions are
not rewritten. Unknown collection hooks are detected before optimizing and we fall back to normal import and collection.
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
from doeff_hy._pytest_items import FunctionItem, Parametrize

from doeff_adr._item_cache import FixtureRecord


# Do not trust names alone; check against the known implementation itself. Wrappers from arbitrary plugins also fall back to the normal path.
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
    # Depending on the pytest version, the fixture manager's report wrapper may or may not exist.
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
    """Share resolution per registration contents within one session. Each item gets a copy of the list/dict.

    The registry is compared right before collecting each module, so lower-level conftest files and additional registrations are not missed.
    Even if the internal fixture array grows in place, the tuple snapshot stays independent.
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
        # Even in the same parent dir, a registration targeting only a specific module/item looks different from its siblings.
        # Compare not only resolved names but also names missing from the closure (the idea of C lookup).
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
    """Item generation limited to the records of one module. The plugin call itself goes through the normal hook."""

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
        # usefixtures without arguments warns on every fixture resolution. Do not share it and skip the warning.
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
            # Known hooks only: for a sync function with no marks and no fixture params, the generation hooks are all no-ops.
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
