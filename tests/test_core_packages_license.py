"""核の 5 個(doeff・doeff-vm・doeff-core-effects・doeff-hy・doeff-hy-pytest)の配布物が、根と同じ MIT の LICENSE を持つ事の
失敗ケース(取り組み doeff-publish の区切り 2・card ki-2dbb91f7e2c8)。

license の欄は PEP 639 の SPDX の式(`license = "MIT"`)と `license-files` で書く。build の口はそれを core metadata の
License-Expression と License-File にし、LICENSE を wheel の dist-info/licenses と sdist の根に入れる。PEP 639 では license の
分類子は廃止の扱いで、License-Expression と並べない。build の口の古い版は License-Expression を書かない(hatchling 1.26 の既定の
metadata は 2.4 より前・maturin 1.9.2 は sdist に license-files を入れない)ので、build-system の下限でその版を断る。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib
from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parents[1]
ROOT_LICENSE = REPO / "LICENSE"

# 核の 5 個の名 → pyproject.toml の在る dir。
CORE = {
    "doeff": REPO,
    "doeff-vm": REPO / "packages" / "doeff-vm",
    "doeff-core-effects": REPO / "packages" / "doeff-core-effects",
    "doeff-hy": REPO / "packages" / "doeff-hy",
    "doeff-hy-pytest": REPO / "packages" / "doeff-hy-pytest",
}

# build の口の名 → PEP 639 の欄を License-Expression と License-File に書かない最後の版(下限はこの版を断る)。
LAST_BACKEND_WITHOUT_PEP639 = {"hatchling": "1.26.3", "maturin": "1.9.2"}


def _table(directory: Path, *path: str) -> dict[str, object]:
    """package の pyproject.toml の入れ子の表(project・build-system・tool.hatch.build.targets.sdist)を、無ければその名で落として
    取り出すため。"""
    table: dict[str, object] = tomllib.loads(
        (directory / "pyproject.toml").read_text(encoding="utf-8")
    )
    for key in path:
        match table.get(key):
            case dict() as inner:
                table = inner
            case _:
                pytest.fail(f"{directory / 'pyproject.toml'} に表 {'.'.join(path)} が無い")
    return table


def _strings(table: dict[str, object], key: str) -> list[str]:
    """表の文字列の列(classifiers・requires・include)を読むため(無ければ空)。"""
    match table.get(key, []):
        case list() as items if all(isinstance(item, str) for item in items):
            return [str(item) for item in items]
        case other:
            pytest.fail(f"{key} が文字列の列でない: {other!r}")


@pytest.mark.parametrize("name", sorted(CORE))
def test_each_core_package_declares_mit_and_ships_the_root_license(name: str) -> None:
    directory = CORE[name]
    project = _table(directory, "project")
    assert project.get("name") == name
    assert project.get("license") == "MIT", (
        f"{name} の license の欄が SPDX の式 MIT でない: {project.get('license')!r}"
    )
    assert project.get("license-files") == ["LICENSE"], (
        f"{name} の license-files が LICENSE でない: {project.get('license-files')!r}"
    )
    license_file = directory / "LICENSE"
    assert license_file.is_file(), f"{name} の dir に LICENSE が無い"
    assert license_file.read_bytes() == ROOT_LICENSE.read_bytes(), (
        f"{name} の LICENSE が根の LICENSE と違う"
    )
    classifiers = [
        line for line in _strings(project, "classifiers") if line.startswith("License ::")
    ]
    assert not classifiers, (
        f"{name} が License-Expression と license の分類子を並べている(PEP 639 で廃止): {classifiers}"
    )


@pytest.mark.parametrize("name", sorted(CORE))
def test_each_core_package_build_backend_writes_the_license_expression(name: str) -> None:
    requires = [
        Requirement(line) for line in _strings(_table(CORE[name], "build-system"), "requires")
    ]
    backends = [
        requirement for requirement in requires if requirement.name in LAST_BACKEND_WITHOUT_PEP639
    ]
    assert len(backends) == 1, (
        f"{name} の build-system の requires に build の口が 1 つでない: {requires}"
    )
    (backend,) = backends
    old = LAST_BACKEND_WITHOUT_PEP639[backend.name]
    assert not backend.specifier.contains(old), (
        f"{name} の build の口の下限 {backend} が、License-Expression を書かない {old} を許す"
    )


def test_the_doeff_sdist_include_is_anchored_to_the_root() -> None:
    # 頭に / の無い pattern は gitignore の形でどの深さの同じ名にも当たり、他の部品の test の data の LICENSE(MIT ではない)まで
    # doeff の sdist に入っていた。
    sdist = _table(REPO, "tool", "hatch", "build", "targets", "sdist")
    loose = [pattern for pattern in _strings(sdist, "include") if not pattern.startswith("/")]
    assert not loose, f"doeff の sdist の include に根に縛らない pattern が在る: {loose}"
