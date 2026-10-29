"""tools/public_api_inventory.py が subpackage の公開名を数え、名ごとに effect・handler・それ以外・未分類を付ける事。

道具は package ごとに子の process で import して読む。ここでは、小さな module を作って種類の付け方(印・戻りの型・
return の形・名)を確かめ、packages/ の下の dir の全部が目録に 1 つずつ載る事と、実物の package(doeff-time・doeff-adr・
doeff-linter・doeff-vm-core)の読み — effect と handler・console script と entry point と Hy の macro・Rust の実行 file・
Python の名の無い package — を確かめる。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    """tools/ の module を読む(tools/ は package ではない)。"""
    spec = importlib.util.spec_from_file_location(
        "public_api_inventory", ROOT / "tools" / "public_api_inventory.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["public_api_inventory"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()

SAMPLE = '''
from collections.abc import Callable
from typing import Any

from doeff import EffectBase, Pass, do, handler
from doeff.program import ProgramHandler


class PingEffect(EffectBase):
    """An effect."""


class SessionHandler:
    """Named like a handler, without the marker."""


class PongEffect:
    """Named like an effect, without being one."""


def ping() -> PingEffect:
    return PingEffect()


def ping_untyped():
    return PingEffect()


@handler
def ping_handler(effect: PingEffect, k):
    yield Pass()


def ping_handler_factory() -> ProgramHandler:
    return ping_handler


def ping_handler_untyped():
    return ping_handler


def raw_ping(effect, k):
    yield Pass()


@do
def flow():
    yield ping()


def helper(x):
    return x + 1


def size() -> int:
    return 3


@do
def raw_ping_program(effect, k):
    yield Pass()


def ping_handler_vague() -> Any:
    return ping_handler


def helper_vague() -> Callable[..., Any]:
    return helper


def ping_handler_stack() -> list[ProgramHandler]:
    return [ping_handler]


def make_session() -> SessionHandler:
    return SessionHandler()


LIMIT = 3
'''

LAZY = """
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventory_sample import PingEffect as LazyPing


def __getattr__(name):
    if name == "LazyPing":
        from inventory_sample import PingEffect

        return PingEffect
    raise AttributeError(name)
"""


@pytest.fixture
def sample(tmp_path: Path) -> Iterator[ModuleType]:
    """SAMPLE を tmp_path の module として import する(inspect.getsource が読めるように file に置く)。"""
    (tmp_path / "inventory_sample.py").write_text(textwrap.dedent(SAMPLE), encoding="utf-8")
    (tmp_path / "inventory_lazy.py").write_text(textwrap.dedent(LAZY), encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        yield importlib.import_module("inventory_sample")
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("inventory_sample", None)
        sys.modules.pop("inventory_lazy", None)


@pytest.mark.parametrize(
    ("name", "kind", "form"),
    [
        ("PingEffect", "effect", "effect の class"),
        ("ping", "effect", "effect を作る関数"),
        ("ping_untyped", "effect", "effect を作る関数"),
        ("ping_handler", "handler", "handler"),
        ("ping_handler_factory", "handler", "handler を返す関数"),
        ("ping_handler_untyped", "handler", "handler を返す関数"),
        ("raw_ping", "handler", "素の handler の関数"),
        ("flow", "other", "program 関数"),
        ("size", "other", "関数"),
        ("LIMIT", "other", "定数"),
        ("SessionHandler", "unclassified", "class"),
        ("PongEffect", "unclassified", "class"),
        ("helper", "unclassified", "関数"),
        ("raw_ping_program", "handler", "素の handler の関数"),
        ("ping_handler_vague", "handler", "handler を返す関数"),
        ("helper_vague", "unclassified", "関数"),
        ("ping_handler_stack", "handler", "handler の組を返す関数"),
        ("make_session", "unclassified", "関数"),
    ],
)
def test_names_are_typed_by_marker_return_type_and_return_shape(
    sample: ModuleType, name: str, kind: str, form: str
) -> None:
    """印・戻りの型・return の形で effect と handler を決め、決まらない物を未分類にする。

    中身を言わない戻りの型(Any・Callable[..., Any])は return の形に任せ、それでも決まらなければ未分類にする。
    """
    verdict = tool._Classifier().classify(sample.__dict__[name], name)
    assert (verdict.kind, verdict.form) == (kind, form)


def test_names_made_on_demand_are_exported(sample: ModuleType) -> None:
    """module の __getattr__ で遅れて出す名も、`if TYPE_CHECKING:` の import が並べる名から数える。"""
    lazy = importlib.import_module("inventory_lazy")
    names, read = tool._exported_names(lazy, tool._Sources(("inventory_lazy", "inventory_sample")))
    assert (names, read) == (["LazyPing"], "own")


def test_every_package_dir_is_listed() -> None:
    """packages/ の下の pyproject.toml か Cargo.toml を持つ dir は、全部が目録の package に入る。"""
    listed = {path.name for path in tool.package_dirs(ROOT)}
    expected = {
        path.name
        for path in (ROOT / "packages").iterdir()
        if (path / "pyproject.toml").is_file() or (path / "Cargo.toml").is_file()
    }
    assert expected <= listed


def test_markdown_has_one_section_per_package() -> None:
    """表の markdown は、目録の package ごとに見出しを 1 つ持つ(package の数と節の数が合う)。"""
    packages = [
        {"dir": name, "import_roots": [], "notes": [], "modules": [], "exports": [], "error": None}
        for name in ("doeff-a", "doeff-b", "doeff-c")
    ]
    page = tool.render_markdown(packages, "0123456789ab")
    assert [line for line in page.splitlines() if line.startswith("## doeff-")] == [
        "## doeff-a",
        "## doeff-b",
        "## doeff-c",
    ]


# 実物の package を子の process で import して読む検の秒の上限(4 package・load 約 170 の下で 30 秒ほど — 既定の 60 秒では
# 負荷の高い機体で fixture の途中に切れる)。
_REAL_PACKAGES_BUDGET = 900


@pytest.fixture(scope="module")
def real_packages() -> dict[str, dict]:
    """実物の 4 package を、道具と同じく子の process で読む(package の dir の名から目録の 1 項へ)。"""
    names = ["doeff-time", "doeff-adr", "doeff-linter", "doeff-vm-core"]
    return {package["dir"]: package for package in tool.inventory(ROOT, names, jobs=4)}


def _rows(package: dict) -> dict[tuple[str, str], dict]:
    """(出す module, 名) から目録の行へ — 行を名で引いて確かめるため。"""
    return {(row["module"], row["name"]): row for row in package["exports"]}


@pytest.mark.timeout(_REAL_PACKAGES_BUDGET)
def test_real_package_effects_and_handlers(real_packages: dict[str, dict]) -> None:
    """doeff-time の effect の class・effect を作る関数・handler を返す関数を、定義の場所つきで読む。"""
    package = real_packages["doeff-time"]
    assert package["error"] is None
    rows = _rows(package)
    assert rows[("doeff_time", "GetTimeEffect")]["kind"] == "effect"
    assert rows[("doeff_time", "GetTimeEffect")]["defined_at"].startswith(
        "packages/doeff-time/src/doeff_time/_effects/time.py:"
    )
    assert rows[("doeff_time", "get_time")]["kind"] == "effect"
    assert rows[("doeff_time", "sim_time_handler")]["kind"] == "handler"
    assert rows[("doeff_time", "SimClock")]["kind"] == "other"


@pytest.mark.timeout(_REAL_PACKAGES_BUDGET)
def test_real_package_scripts_entry_points_and_macros(
    real_packages: dict[str, dict],
) -> None:
    """doeff-adr の console script・pytest の entry point・Hy の macro を数える。"""
    rows = _rows(real_packages["doeff-adr"])
    assert rows[("(console script)", "doeff-adr")]["form"] == "console script"
    assert rows[("(entry point)", "pytest11:doeff_adr")]["form"] == "entry point(pytest11)"
    assert rows[("doeff_adr.macros", "defadr")]["form"] == "Hy の macro"


@pytest.mark.timeout(_REAL_PACKAGES_BUDGET)
def test_rust_and_python_less_packages_are_listed(
    real_packages: dict[str, dict],
) -> None:
    """Rust の実行 file だけの package と、Python の名の無い package も目録に載る。"""
    linter = _rows(real_packages["doeff-linter"])
    assert linter[("(実行 file)", "doeff-linter")]["form"] == "Rust の実行 file"
    core = real_packages["doeff-vm-core"]
    assert core["exports"] == []
    assert core["notes"]
