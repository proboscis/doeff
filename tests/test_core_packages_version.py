"""核の 5 個(doeff・doeff-vm・doeff-core-effects・doeff-hy・doeff-hy-pytest)の版の番号が 1 つに揃い、互いの依存がその番号の
系に限られる事の失敗ケース(取り組み doeff-publish の区切り 3・card ki-4f3135e2be74)。

5 個は 1 つの番号で出す — 番号 1 つで、合う組み合わせが分かる(範囲と手順の報告の節「版の付け方」)。版を持つ所は 8 か所:
5 個の pyproject の project.version・doeff の __version__・doeff-vm の Cargo.toml の package.version と Cargo.lock の doeff-vm の行。
互いの依存(dependencies と optional-dependencies)は、その番号の 2 桁の系に限る(0.5.0 なら >=0.5.0,<0.6)— PyPI から入れた
時に、別の系の部品と組み合わさらない。手元の開発では [tool.uv.sources] の workspace の指定がその依存を作業木の部品へ解くので、
版の指定と並べて残す。
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest
import tomllib
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parents[1]
VM = REPO / "packages" / "doeff-vm"

# 核の 5 個の名 → pyproject.toml の在る dir(tests/test_core_packages_license.py と同じ組)。
CORE = {
    "doeff": REPO,
    "doeff-vm": VM,
    "doeff-core-effects": REPO / "packages" / "doeff-core-effects",
    "doeff-hy": REPO / "packages" / "doeff-hy",
    "doeff-hy-pytest": REPO / "packages" / "doeff-hy-pytest",
}


def _toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _project(name: str) -> dict[str, object]:
    match _toml(CORE[name] / "pyproject.toml").get("project"):
        case dict() as project:
            return project
        case _:
            pytest.fail(f"{name} の pyproject.toml に表 project が無い")


def _pyproject_version(name: str) -> str:
    match _project(name).get("version"):
        case str() as version:
            return version
        case other:
            pytest.fail(
                f"{name} の project.version が文字列でない(dynamic の版はこの検が読めない): {other!r}"
            )


def _dunder_version() -> str:
    tree = ast.parse((REPO / "doeff" / "__init__.py").read_text(encoding="utf-8"))
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    assert len(values) == 1, (
        f"doeff/__init__.py の上の段に __version__ = '<版>' が 1 つでない: {values}"
    )
    return values[0]


def _cargo_version() -> str:
    match _toml(VM / "Cargo.toml").get("package"):
        case {"version": str() as version}:
            return version
        case other:
            pytest.fail(
                f"packages/doeff-vm/Cargo.toml の package.version が文字列でない: {other!r}"
            )


def _cargo_lock_version() -> str:
    match _toml(VM / "Cargo.lock").get("package"):
        case list() as packages:
            versions = [
                package.get("version")
                for package in packages
                if isinstance(package, dict) and package.get("name") == "doeff-vm"
            ]
        case other:
            pytest.fail(f"packages/doeff-vm/Cargo.lock に package の列が無い: {other!r}")
    match versions:
        case [str() as version]:
            return version
        case _:
            pytest.fail(f"packages/doeff-vm/Cargo.lock の doeff-vm の行が 1 つでない: {versions}")


# 版を持つ所(repo の根からの path と欄)→ その所の版を読む関数。比べる基準は doeff の pyproject の版(公開の tag v<版> の番号)。
PLACES: dict[str, Callable[[], str]] = {
    **{
        f"{(CORE[name] / 'pyproject.toml').relative_to(REPO)}:project.version": partial(
            _pyproject_version, name
        )
        for name in CORE
        if name != "doeff"
    },
    "doeff/__init__.py:__version__": _dunder_version,
    "packages/doeff-vm/Cargo.toml:package.version": _cargo_version,
    "packages/doeff-vm/Cargo.lock:package.doeff-vm": _cargo_lock_version,
}


def _series(version: str) -> SpecifierSet:
    """版の番号の 2 桁の系(0.5.0 → >=0.5.0,<0.6)。"""
    major, minor = (*Version(version).release, 0)[:2]
    return SpecifierSet(f">={major}.{minor}.0,<{major}.{minor + 1}")


def _strings(table: dict[str, object], key: str, where: str) -> list[str]:
    match table.get(key, []):
        case list() as items if all(isinstance(item, str) for item in items):
            return [str(item) for item in items]
        case other:
            pytest.fail(f"{where} が文字列の列でない: {other!r}")


def _core_requirements(name: str) -> list[tuple[str, Requirement]]:
    """name の依存(dependencies と optional-dependencies の各 extra)のうち核の部品への物を、書いた欄の名と共に読むため。"""
    project = _project(name)
    rows = [
        ("dependencies", line)
        for line in _strings(project, "dependencies", f"{name} の dependencies")
    ]
    match project.get("optional-dependencies", {}):
        case dict() as extras:
            for extra in sorted(extras):
                where = f"optional-dependencies.{extra}"
                rows.extend((where, line) for line in _strings(extras, extra, f"{name} の {where}"))
        case other:
            pytest.fail(f"{name} の optional-dependencies が表でない: {other!r}")
    requirements = [(where, Requirement(line)) for where, line in rows]
    return [(where, req) for where, req in requirements if canonicalize_name(req.name) in CORE]


def _sources(directory: Path) -> dict[str, object]:
    match _toml(directory / "pyproject.toml").get("tool", {}):
        case {"uv": {"sources": dict() as sources}}:
            return {canonicalize_name(key): value for key, value in sources.items()}
        case _:
            return {}


# 下の検は所ごと・package ごとに分けず、ずれた所を全部並べて 1 本で落とす — 契約の検の表の repo 全体の行で毎回走るので、本数を
# 少なく保つ(所と package ごとに分けた 18 本は、load 約 115 の下で根の conftest と共に 16.7 秒かかった)。


def test_every_place_holding_a_core_version_holds_the_doeff_version() -> None:
    expected = _pyproject_version("doeff")
    drifted = {place: version for place, read in PLACES.items() if (version := read()) != expected}
    assert not drifted, f"核の 5 個は 1 つの番号で出す — doeff の版 {expected} と違う所: {drifted}"


def test_each_core_dependency_is_limited_to_the_series_of_the_doeff_version() -> None:
    series = _series(_pyproject_version("doeff"))
    wrong = [
        f"{name} の {where}: {req}"
        for name in CORE
        for where, req in _core_requirements(name)
        if req.url is not None or req.specifier != series
    ]
    assert not wrong, f"核の部品への依存が系 {series} でない: {wrong}"


def test_each_core_dependency_resolves_to_the_workspace_member_in_development() -> None:
    root = _sources(REPO)
    loose = [
        f"{name} → {req.name}"
        for name in CORE
        for _, req in _core_requirements(name)
        # uv は member の [tool.uv.sources] を先に、無い名は workspace の根の [tool.uv.sources] を引く。
        if {**root, **_sources(CORE[name])}.get(canonicalize_name(req.name)) != {"workspace": True}
    ]
    assert not loose, (
        f"核の部品への依存 {loose} が workspace の source に解かれない — 手元の開発で PyPI の版を引く"
    )


def test_the_dependency_checks_read_the_core_edges() -> None:
    # 依存の読みが 1 本も拾わないと、上の 2 つの検は黙って緑になる(例 依存を読まない欄へ移した時)。
    edges = sorted(
        (name, canonicalize_name(req.name)) for name in CORE for _, req in _core_requirements(name)
    )
    assert ("doeff", "doeff-vm") in edges, (
        f"核の部品どうしの依存の読みに doeff → doeff-vm が無い: {edges}"
    )
