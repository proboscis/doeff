"""subpackage の公開 API に effect と handler の外の名を足すと赤になり、今の外れは減るだけの既知の表に並ぶ事。

決まり: doeff の subpackage が利用者に見せる公開 API は effect と handler だけ(取り組み doeff-subpackages-effects-only)。
公開 API を数えるのは tools/public_api_inventory.py の 1 か所で、この file は数えない — 道具の目録の行のうち、種類が
effect でも handler でもない物(それ以外・未分類)を外れとし、tests/data/public_api_known_violations.json の既知の表と
(名を出す module, 名) の鍵で突き合わせる:

1. package ごとに 1 件(packages/ の下の dir の名で引く): 表に無い外れが在れば赤(新しく足した外れ)、表に在るのに
   目録に無い名が在れば赤(直したのに表を縮め忘れた)。
2. 表の形: packages/ の下の dir の全部が 1 つずつ載り、範囲が決まった値で、名が組の中で並んで重ならない。
3. 表は減るだけ: 表の「名」の組の名は、この表を足した commit の版(git の履歴の最初の版)に在る名に限る。新しく足すしか
   無い名は「足した名」に、訳と決め(card・依頼・利用者の発言の id)を 1 行ずつ書く。
4. 失敗ケース: 使い捨ての package を作り、effect と handler だけなら緑・外れを 1 つ足すと赤・外すと緑・直したのに表に
   残すと赤・訳と決めつきで「足した名」に書くと緑になる事を、同じ照合で確かめる。
"""

from __future__ import annotations

import collections
import importlib.util
import json
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, NotRequired, TypedDict

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "tests" / "data" / "public_api_known_violations.json"
#: 目録の種類のうち、決まりの外の物(effect と handler の外)。
OUTSIDE_KINDS = frozenset({"other", "unclassified"})
#: package の範囲。範囲の外の package は名を突き合わせない(決めの id が要る)。
SCOPES = ("決まりの範囲", "範囲の答え待ち", "範囲の外")
#: 名の組の名の頭(分け — 直しの向きか境界の種類)。
GROUP_HEADS = ("外れ — ", "境界 — ", "未分け — ")
#: 決めの id: card(ki-)・依頼(lt-)・利用者の発言(ci-)。
DECISION = re.compile(r"(ki-[0-9a-f]{12}|lt-[0-9A-Z]{26}|ci-[0-9A-Z]{26})")
#: 1 package を子の process で読む秒の上限(load 約 55 の下で最も重い doeff-cluster が 9 秒・混みの倍率は根の conftest が掛ける)。
_PROBE_SECONDS = 100

Key = tuple[str, str]


class AddedName(TypedDict):
    """「足した名」の 1 行: 表を足した後に足すしか無かった外れ(訳と決めの id つき)。"""

    package: str
    module: str
    name: str
    訳: str
    決め: str


class PackageEntry(TypedDict):
    """1 package の項: 範囲と、組の名(分け — 直しの向きか境界の種類)から module から名の列へ。範囲の外は決めの id を持つ。"""

    範囲: str
    名: dict[str, dict[str, list[str]]]
    決め: NotRequired[str]


class KnownTable(TypedDict):
    """既知の表の全体(説明と問いは読む人のための欄)。"""

    説明: NotRequired[list[str]]
    問い: NotRequired[str]
    packages: dict[str, PackageEntry]
    足した名: list[AddedName]


def _load() -> ModuleType:
    """tools/ の目録の道具を読む(tools/ は package ではない — 道具の検と同じ名で 1 つを共にする)。"""
    loaded = sys.modules.get("public_api_inventory")
    if loaded is not None:
        return loaded
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


def _read_table(path: Path = TABLE) -> KnownTable:
    return json.loads(path.read_text(encoding="utf-8"))


def listed_names(table: KnownTable) -> set[tuple[str, str, str]]:
    """表の「名」の組に在る (package の dir, module, 名) の全部。"""
    return {
        (package_dir, module, name)
        for package_dir, entry in table["packages"].items()
        for modules in entry["名"].values()
        for module, names in modules.items()
        for name in names
    }


def known_names(table: KnownTable, package_dir: str) -> set[Key]:
    """1 package の既知の外れ: 「名」の組の名と「足した名」の名。"""
    listed = {
        (module, name)
        for modules in table["packages"][package_dir]["名"].values()
        for module, names in modules.items()
        for name in names
    }
    added = {
        (row["module"], row["name"]) for row in table["足した名"] if row["package"] == package_dir
    }
    return listed | added


def outside_rows(package: Mapping[str, Any]) -> dict[Key, Mapping[str, Any]]:
    """目録の 1 package の行のうち、effect と handler の外の物((module, 名) から行へ)。"""
    return {
        (row["module"], row["name"]): row
        for row in package["exports"]
        if row["kind"] in OUTSIDE_KINDS
    }


def _new_text(directory: str, new: list[Key], found: Mapping[Key, Mapping[str, Any]]) -> str:
    rows = [found[key] for key in new]
    return (
        f"{directory}: 既知の表に無い、effect と handler の外の公開名が {len(new)} 個 — effect か handler にするか、"
        "名を _ で始めるか _ で始まる module へ移す。どうしても見せるなら、表の「足した名」に訳と決めつきで書く:\n"
        + "\n".join(
            f"  {row['module']}.{row['name']}({row['form']}・{row['kind']}・{row['defined_at']})"
            for row in rows
        )
    )


def _stale_text(directory: str, stale: list[Key]) -> str:
    return (
        f"{directory}: 既知の表に在るのに目録に無い名が {len(stale)} 個 — 直した名は表から消す(表は減るだけ):\n"
        + "\n".join(f"  {module}.{name}" for module, name in stale)
    )


def check_package(package: Mapping[str, Any], table: KnownTable) -> None:
    """目録の 1 package を既知の表と突き合わせる。表に無い外れと、表に在るのに目録に無い名を名指して止める。"""
    directory = package["dir"]
    assert package["error"] is None, f"{directory} を読めなかった: {package['error']}"
    entry = table["packages"].get(directory)
    assert entry is not None, (
        f"{directory} が既知の表 {TABLE.relative_to(ROOT)} に無い — package を足したら範囲と名の組を表に足す"
    )
    if entry["範囲"] == "範囲の外":
        return
    found = outside_rows(package)
    known = known_names(table, directory)
    new = sorted(set(found) - known)
    stale = sorted(known - set(found))
    problems = [
        *([_new_text(directory, new, found)] if new else []),
        *([_stale_text(directory, stale)] if stale else []),
    ]
    assert not problems, "\n".join(problems)


def _added_row_problems(
    row: AddedName, table: KnownTable, listed: set[tuple[str, str, str]]
) -> list[str]:
    """「足した名」の 1 行の誤り: 欄・package・訳・決め・「名」の組との重なり。"""
    where = f"足した名 {row.get('package')} {row.get('module')}.{row.get('name')}"
    if set(row) != set(AddedName.__annotations__):
        return [f"{where}: 欄は package・module・name・訳・決め"]
    checks = (
        (row["package"] not in table["packages"], "package が表に無い"),
        (not row["訳"].strip(), "訳が空"),
        (
            DECISION.fullmatch(row["決め"]) is None,
            f"決め {row['決め']!r} が card(ki-)・依頼(lt-)・発言(ci-)の id でない",
        ),
        ((row["package"], row["module"], row["name"]) in listed, "名の組にも在る"),
    )
    return [f"{where}: {text}" for failed, text in checks if failed]


def shape_problems(table: KnownTable, package_dirs: Iterable[str]) -> list[str]:
    """表の形の誤り: dir の過不足・範囲・組の名・名の並びと重なり・「足した名」の訳と決め。"""
    dirs = set(package_dirs)
    listed_dirs = set(table["packages"])
    entries = sorted(table["packages"].items())
    placements = {
        (package_dir, group, module, name)
        for package_dir, entry in entries
        for group, modules in entry["名"].items()
        for module, names in modules.items()
        for name in names
    }
    groups_per_name = collections.Counter(
        (package_dir, module, name) for package_dir, _group, module, name in placements
    )
    return [
        *(f"packages/ の下に在るのに表に無い: {name}" for name in sorted(dirs - listed_dirs)),
        *(f"表に在るのに packages/ の下に無い: {name}" for name in sorted(listed_dirs - dirs)),
        *(
            f"{package_dir}: 範囲 {entry.get('範囲')!r} は {SCOPES} のどれでもない"
            for package_dir, entry in entries
            if entry.get("範囲") not in SCOPES
        ),
        *(
            f"{package_dir}: 範囲の外の package は決めの id を持ち、名の組を持たない"
            for package_dir, entry in entries
            if entry.get("範囲") == "範囲の外"
            and (DECISION.search(entry.get("決め", "")) is None or entry["名"])
        ),
        *(
            f"{package_dir}: 組の名 {group!r} が {GROUP_HEADS} のどれでも始まらない"
            for package_dir, entry in entries
            for group in entry["名"]
            if not group.startswith(GROUP_HEADS)
        ),
        *(
            f"{package_dir} {group} {module}: 名は空でなく、並べて重ねない"
            for package_dir, entry in entries
            for group, modules in entry["名"].items()
            for module, names in modules.items()
            if not names or names != sorted(set(names))
        ),
        *(
            f"{package_dir}: {module}.{name} が 2 つ以上の組に在る"
            for (package_dir, module, name), count in sorted(groups_per_name.items())
            if count > 1
        ),
        *(
            problem
            for row in table["足した名"]
            for problem in _added_row_problems(row, table, set(groups_per_name))
        ),
    ]


def grown_names(first: KnownTable, current: KnownTable) -> list[tuple[str, str, str]]:
    """最初の版の「名」の組に無いのに、今の「名」の組に在る名(表が増えた所)。"""
    return sorted(listed_names(current) - listed_names(first))


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *arguments], capture_output=True, text=True, check=True
    ).stdout


def first_version() -> KnownTable | None:
    """既知の表を足した commit の版(git の履歴の最初の版)。まだ commit していない間は None。"""
    relative = TABLE.relative_to(ROOT).as_posix()
    added = _git("log", "--diff-filter=A", "--format=%H", "--", relative).split()
    if not added:
        assert not _git("ls-files", "--", relative).strip(), (
            f"{relative} は追跡されているのに、足した commit が履歴に無い — 減るだけの比べの相手が読めない"
        )
        return None
    return json.loads(_git("show", f"{added[-1]}:{relative}"))


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize("package_dir", [path.name for path in tool.package_dirs(ROOT)])
def test_public_api_outside_effects_and_handlers_is_in_the_known_table(package_dir: str) -> None:
    """package の公開名のうち effect と handler の外の物が、既知の表の名とちょうど同じ(足しても直しても表と合わせる)。"""
    [package] = tool.inventory(ROOT, [package_dir], jobs=1, timeout=_PROBE_SECONDS)
    check_package(package, _read_table())


def test_the_known_table_has_one_well_formed_entry_per_package_dir() -> None:
    """表は packages/ の下の dir を 1 つずつ持ち、範囲・組の名・名の並び・「足した名」の訳と決めが形どおり。"""
    problems = shape_problems(_read_table(), (path.name for path in tool.package_dirs(ROOT)))
    assert not problems, "\n".join(problems)


def test_the_known_table_only_shrinks_from_its_first_version() -> None:
    """表の「名」の組は、表を足した commit の版から減るだけ(新しく足す名は「足した名」に訳と決めつきで書く)。"""
    first = first_version()
    if first is None:
        return
    grown = grown_names(first, _read_table())
    assert not grown, (
        "既知の表の「名」の組に、最初の版に無い名が在る — 表は減るだけ。足すしか無い名は「足した名」に訳と決めつきで書く:\n"
        + "\n".join(f"  {package_dir} {module}.{name}" for package_dir, module, name in grown)
    )


# --- 失敗ケース: 使い捨ての package で、同じ照合が赤と緑を返す事 ---

FIXTURE_DIR = "doeff-fixture-api"
FIXTURE_ROOT = "doeff_fixture_api"
FIXTURE_PYPROJECT = f"""
[project]
name = "{FIXTURE_DIR}"
version = "0"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{FIXTURE_ROOT}"]
"""
EFFECTS_AND_HANDLERS = '''
from doeff import EffectBase, Pass, handler


class PingEffect(EffectBase):
    """The one effect."""


@handler
def ping_handler(effect: PingEffect, k):
    yield Pass()
'''
OUTLIER = '''

def helper(x: int) -> int:
    """A plain function: neither an effect nor a handler."""
    return x + 1
'''


def _fixture_repo(tmp_path: Path, source: str) -> Path:
    """packages/ の下に package を 1 つ持つ使い捨ての repo(__init__ の中身は source)。"""
    package = tmp_path / "packages" / FIXTURE_DIR
    (package / "src" / FIXTURE_ROOT).mkdir(parents=True, exist_ok=True)
    (package / "pyproject.toml").write_text(textwrap.dedent(FIXTURE_PYPROJECT), encoding="utf-8")
    (package / "src" / FIXTURE_ROOT / "__init__.py").write_text(
        textwrap.dedent(source), encoding="utf-8"
    )
    return tmp_path


def _fixture_table(
    names: Mapping[str, list[str]], added: list[AddedName] | None = None
) -> KnownTable:
    groups = {"外れ — 内側の module へ移す": dict(names)} if names else {}
    return {
        "packages": {FIXTURE_DIR: {"範囲": "決まりの範囲", "名": groups}},
        "足した名": added or [],
    }


def _read_fixture(repo: Path) -> dict[str, Any]:
    [package] = tool.inventory(repo, [FIXTURE_DIR], jobs=1, timeout=_PROBE_SECONDS)
    return package


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_added_outlier_turns_red_and_removing_it_turns_green(tmp_path: Path) -> None:
    """effect と handler だけの package は緑・外れの関数を 1 つ足すと赤(名を名指す)・外すとまた緑。"""
    table = _fixture_table({})
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS)
    check_package(_read_fixture(repo), table)

    _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + OUTLIER)
    with pytest.raises(AssertionError, match=r"既知の表に無い[\s\S]*doeff_fixture_api\.helper"):
        check_package(_read_fixture(repo), table)

    _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS)
    check_package(_read_fixture(repo), table)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_fixed_name_left_in_the_table_turns_red(tmp_path: Path) -> None:
    """表に在る外れを直した(消した)のに表に残すと赤 — 表を縮めるのを忘れた所を名指す。"""
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS)
    table = _fixture_table({FIXTURE_ROOT: ["helper"]})
    with pytest.raises(AssertionError, match=r"目録に無い名[\s\S]*doeff_fixture_api\.helper"):
        check_package(_read_fixture(repo), table)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_outlier_added_with_a_reason_line_is_green(tmp_path: Path) -> None:
    """足すしか無い外れは「足した名」に訳と決めつきで書けば緑。訳か決めが欠けると表の形の検が赤。"""
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + OUTLIER)
    row: AddedName = {
        "package": FIXTURE_DIR,
        "module": FIXTURE_ROOT,
        "name": "helper",
        "訳": "使い手が値を直に組む所(例)",
        "決め": "ki-aaaa56621c5a",
    }
    table = _fixture_table({}, [row])
    check_package(_read_fixture(repo), table)
    assert shape_problems(table, [FIXTURE_DIR]) == []

    lacking = _fixture_table({}, [{**row, "訳": " ", "決め": "決めた"}])
    assert shape_problems(lacking, [FIXTURE_DIR]) == [
        f"足した名 {FIXTURE_DIR} {FIXTURE_ROOT}.helper: 訳が空",
        f"足した名 {FIXTURE_DIR} {FIXTURE_ROOT}.helper: 決め '決めた' が card(ki-)・依頼(lt-)・発言(ci-)の id でない",
    ]


def test_a_name_put_into_the_listed_groups_turns_red() -> None:
    """最初の版の「名」の組に無い名を「名」の組に足すと、減るだけの検が名指す(消すのは緑)。"""
    first = _fixture_table({FIXTURE_ROOT: ["a", "b"]})
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a"]})) == []
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a", "b", "c"]})) == [
        (FIXTURE_DIR, FIXTURE_ROOT, "c")
    ]
