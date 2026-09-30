"""記録からの item 生成が使う pytest 内部 API の境界。

未知の収集 hook は通常の Module.collect に戻す。キャッシュは config の寿命に限定し、
使う前に fixture の可視性と追加登録を pytest の lookup で確認する。pytest 自体は変更しない。
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from _pytest.fixtures import FuncFixtureInfo
from doeff_hy.pytest_items import FunctionItem, Record

from doeff_adr.item_cache import FixtureRecord

# この実装の同期関数に対する動作を確認した hook の形。追加・改名は通常経路へ戻す。
_SYNC_NOOPS = frozenset({
    ("pytest_asyncio.plugin", "pytest_pycollect_makeitem_convert_async_functions_to_subclass", True),
    ("pytest_asyncio.plugin", "pytest_generate_tests", False),
    ("anyio.pytest_plugin", "pytest_pycollect_makeitem", False),
})


def supports_record_collection(module: pytest.Module) -> bool:
    """標準の同期関数収集であることが分かる hook だけを許可する。"""
    for hook in (
        module.ihook.pytest_pycollect_makeitem, module.ihook.pytest_generate_tests,
        module.ihook.pytest_collectstart, module.ihook.pytest_make_collect_report,
    ):
        for implementation in hook.get_hookimpls():
            owner = implementation.function.__module__
            if owner.startswith("_pytest."):
                continue
            shape = (owner, implementation.function.__name__, implementation.hookwrapper)
            if shape in _SYNC_NOOPS and not implementation.wrapper:
                # 記録の stub は同期関数。asyncio / anyio の変換対象にはならない。
                continue
            return False
    return True


def copy_fixture_info(info: FuncFixtureInfo) -> FuncFixtureInfo:
    """parametrize が変える可変コンテナを item 間で共有しない。"""
    return FuncFixtureInfo(
        info.argnames, info.initialnames, list(info.names_closure),
        {name: tuple(definitions) for name, definitions in info.name2fixturedefs.items()},
    )


@dataclass
class FixtureTemplates:
    """引数・autouse が同じで、見える FixtureDef も同じ場合だけ解決結果を再利用する。"""

    entries: dict[tuple[tuple[str, ...], tuple[str, ...]], FuncFixtureInfo] = field(
        default_factory=dict,
    )

    def lookup(
        self, module: pytest.Module, argnames: tuple[str, ...], automatic: tuple[str, ...],
    ) -> FuncFixtureInfo | None:
        info = self.entries.get((argnames, automatic))
        if info is None:
            return None
        manager = module.session._fixturemanager
        # 欠けていた fixture の追加登録も拾うため、解決済みの名だけではなく closure 全体を照合する。
        for name in info.names_closure:
            visible = manager.getfixturedefs(name, module)
            if tuple(visible or ()) != tuple(info.name2fixturedefs.get(name, ())):
                return None
        return copy_fixture_info(info)


_FIXTURE_TEMPLATES = pytest.StashKey[FixtureTemplates]()


def collect_recorded_functions(
    module: pytest.Module, records: tuple[Record, ...], *, fixtures: tuple[FixtureRecord, ...],
) -> Iterator[pytest.Function]:
    """非 parametrize は Function 1 個、parametrize は pytest の Metafunc 経路で展開する。"""
    manager = module.session._fixturemanager
    if fixtures:
        manager.parsefactories(module)
    cache = module.config.stash.setdefault(_FIXTURE_TEMPLATES, FixtureTemplates())
    automatic = tuple(manager._getautousenames(module))
    # dict の更新は、最後の値と最初の挿入位置を残す(Python module と同じ)。
    functions = {record.name: record for record in records if isinstance(record, FunctionItem)}
    for name, record in functions.items():
        if not module.funcnamefilter(name):
            continue
        function = vars(module.obj)[name]
        if record.decorators or tuple(module.iter_markers("usefixtures")):
            yield from module._genfunctions(name, function)
            continue
        info = cache.lookup(module, record.argnames, automatic)
        item = pytest.Function.from_parent(module, name=name, callobj=function, fixtureinfo=info)
        info = item._fixtureinfo
        if any(definition.params is not None
               for definitions in info.name2fixturedefs.values() for definition in definitions):
            yield from module._genfunctions(name, function)
            continue
        cache.entries[(record.argnames, automatic)] = copy_fixture_info(info)
        yield item
