"""subpackage の公開 API に effect と handler の外の名を足すと赤になり、今の外れは減るだけの既知の表に並ぶ事。

決まり: doeff の subpackage が利用者に見せる公開 API は effect と handler だけ(取り組み doeff-subpackages-effects-only)。
公開 API を数えるのは tools/public_api_inventory.py の 1 か所で、この file は数えない — 道具の目録の行のうち、種類が
effect でも handler でもない物(それ以外・未分類)を外れとし、tests/data/public_api_known_violations.json の既知の表と
(名を出す module, 名) の鍵で突き合わせる。_ で始まらない submodule そのものも、目録の 1 行(名を出す module は
(submodule)・名は submodule の import の名)の外れと数える(ADR-DOE-API-001 R5 — 公開の名は import の根からだけ出す):

1. package ごとに 1 件(packages/ の下の dir の名で引く): 表に無い外れが在れば赤(新しく足した外れ・公開の submodule)、
   表に在るのに目録に無い名が在れば赤(直したのに表を縮め忘れた)。
2. 名を submodule から入口へ移した行は、表の module を書き換えずに同じ行と読む: 表に無い根の (根, 名) は、表に同じ名の
   (根の下の submodule, 名) が在り、その submodule がまだ名を出していれば定義の場所が同じ時だけ、もう出していなければ
   (古い口を閉じた — 定義を比べる相手が無い)名と根だけで、表のその行と読む。違う定義を根に足すのは赤のまま。
3. 表の形: packages/ の下の dir の全部が 1 つずつ載り、範囲が決まった値で、名が組の中で並んで重ならない。
4. 表は減るだけ: 表の「名」の組の名は、この表を足した commit の版(git の履歴の最初の版)に在る名に限る。submodule の行は、
   submodule の行を表に足した commit の版に在る物に限る。新しく足すしか無い名は「足した名」に、訳と決め(card・依頼・
   利用者の発言の id)を 1 行ずつ書く。
5. 失敗ケース: 使い捨ての package を作り、effect と handler だけなら緑・外れを 1 つ足すと赤・外すと緑・直したのに表に
   残すと赤・訳と決めつきで「足した名」に書くと緑・_ の無い submodule を足すと赤・名を submodule から入口へ移しても緑
   (移し替えの module を残す間も消した後も)・同じ名の違う定義を入口に足すと赤・1 行目に印 moved-submodule-shim の在る
   移し替えの module は、移した先の package の下の module も古い名で数えて緑になる事を、同じ照合で確かめる。
   戻りの型で handler を返す関数(工場)と読める名が、呼ぶと印 _doeff_is_handler_fn の無い値を返すと赤・handler を返すように
   直すと緑(目録は引数なしで呼べる工場を呼んで印を確かめる — ADR-DOE-API-001 R3)。
6. 組「許した物 — program の helper」(ADR-DOE-API-001 R18 — 決まりが名ごとに許した、effect を出すだけの program を返す関数)
   に置けるのは、目録の形が program の helper の名だけ: handler を被せる・effect に答える・program を返さない関数を置くと赤。
   許した物の組は、目録で形を確かめる組(ALLOWED_GROUPS)だけを置ける。
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
#: 名の組の名の頭(分け — 直しの向きか境界の種類か、決まりが許した物)。
GROUP_HEADS = ("外れ — ", "境界 — ", "未分け — ", "許した物 — ")
#: 決まりが名ごとに許した program の helper の組(ADR-DOE-API-001 R18)— 目録の形が program の helper の名だけを置ける。
HELPER_GROUP = "許した物 — program の helper"
#: 許した物の組の全部(組ごとに、目録で形を確かめる — 確かめの無い組は置けない)。
ALLOWED_GROUPS = (HELPER_GROUP,)
#: 決めの id: card(ki-)・依頼(lt-)・利用者の発言(ci-)。
DECISION = re.compile(r"(ki-[0-9a-f]{12}|lt-[0-9A-Z]{26}|ci-[0-9A-Z]{26})")
#: 1 package を子の process で読む秒の上限(load 約 55 の下で最も重い doeff-cluster が 9 秒・混みの倍率は根の conftest が掛ける)。
_PROBE_SECONDS = 100

Key = tuple[str, str]


class AddedName(TypedDict):
    """「足した名」の 1 行: 表を足した後に足すしか無かった外れ(訳と決めの id つき)。

    表を足した後に許した物(新しく見せる program の helper)は、欄「組」に許した物の組の名を書き、その組と同じ確かめを受ける
    (「名」の組は減るだけなので、許した物を後から足す道はここだけ)。
    """

    package: str
    module: str
    name: str
    訳: str
    決め: str
    組: NotRequired[str]


#: 「足した名」の 1 行に要る欄と、書いてよい欄(組は許した物の時だけ)。
ADDED_FIELDS = frozenset({"package", "module", "name", "訳", "決め"})
ADDED_OPTIONAL_FIELDS = frozenset({"組"})


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
#: 公開の submodule の行の、名を出す module の欄(名は submodule の import の名)。
SUBMODULE: str = tool.SUBMODULE


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


def helper_names(table: KnownTable, package_dir: str) -> set[Key]:
    """1 package の、組「許した物 — program の helper」に置いた (module, 名) と、組をそれと書いた「足した名」の名。"""
    modules = table["packages"][package_dir]["名"].get(HELPER_GROUP, {})
    added = {
        (row["module"], row["name"])
        for row in table["足した名"]
        if row["package"] == package_dir and row.get("組") == HELPER_GROUP
    }
    return {(module, name) for module, names in modules.items() for name in names} | added


def outside_rows(package: Mapping[str, Any]) -> dict[Key, Mapping[str, Any]]:
    """目録の 1 package の行のうち、effect と handler の外の物((module, 名) から行へ)。"""
    return {
        (row["module"], row["name"]): row
        for row in package["exports"]
        if row["kind"] in OUTSIDE_KINDS
    }


def moved_to_root(
    roots: Iterable[str], found: Mapping[Key, Mapping[str, Any]], known: set[Key]
) -> set[tuple[Key, Key]]:
    """名を submodule から入口へ移した行: 表に無い目録の (根, 名) と、同じ行と読む表の (根の下の submodule, 名) の組。

    submodule がまだ同じ名を出していれば(移し替えの module が古い口を開けている間)、定義の場所が同じ時だけ同じ行と読む。
    もう出していなければ(古い口を閉じた後)、比べる定義が無いので名と根だけで読む。
    """
    return {
        ((root, name), (module, name))
        for root, name in set(found) - known
        if root in roots
        for module, known_name in known
        if known_name == name
        and module.startswith(f"{root}.")
        and (
            (module, name) not in found
            or found[(module, name)]["defined_at"] == found[(root, name)]["defined_at"]
        )
    }


def _key_text(module: str, name: str) -> str:
    """表の鍵 1 つを人の読む形に(公開の submodule の行は submodule の名だけ)。"""
    return f"{name}(公開の submodule)" if module == SUBMODULE else f"{module}.{name}"


def _new_text(directory: str, new: list[Key], found: Mapping[Key, Mapping[str, Any]]) -> str:
    rows = [found[key] for key in new]
    return (
        f"{directory}: 既知の表に無い、effect と handler の外の公開名が {len(new)} 個 — effect か handler にするか、"
        "名を _ で始めるか _ で始まる module へ移す(公開の submodule は名に _ を付けて隠し、見せる名は入口から出す)。"
        "どうしても見せるなら、表の「足した名」に訳と決めつきで書く:\n"
        + "\n".join(
            f"  {_key_text(row['module'], row['name'])}({row['form']}・{row['kind']}・{row['defined_at']})"
            for row in rows
        )
    )


def _stale_text(directory: str, stale: list[Key]) -> str:
    return (
        f"{directory}: 既知の表に在るのに目録に無い名が {len(stale)} 個 — 直した名と隠した submodule は表から消す"
        "(表は減るだけ):\n" + "\n".join(f"  {_key_text(module, name)}" for module, name in stale)
    )


def _not_helper_text(directory: str, rows: list[Mapping[str, Any]]) -> str:
    return (
        f"{directory}: 組「{HELPER_GROUP}」(と、組をそれと書いた「足した名」)に、目録の形が {tool.PROGRAM_HELPER} でない名が {len(rows)} 個 — "
        "program を返し、中で handler を被せず effect に答えない関数だけを置ける(ADR-DOE-API-001 R18)。"
        "直すか、組から外して外れか境界の組へ戻す:\n"
        + "\n".join(
            f"  {_key_text(row['module'], row['name'])}({row['form']}・{row['reason']})"
            for row in rows
        )
    )


def check_package(package: Mapping[str, Any], table: KnownTable) -> None:
    """目録の 1 package を既知の表と突き合わせる。表に無い外れと、表に在るのに目録に無い名を名指して止める。

    名を submodule から入口へ移した行(moved_to_root)は、表の submodule の行と同じ行と読む。組「許した物 — program の
    helper」の名は、目録の形が program の helper でなければ名指して止める(入口へ移した行は入口の行の形で見る)。
    """
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
    moved = moved_to_root(package["import_roots"], found, known)
    new = sorted(set(found) - known - {root for root, _old in moved})
    stale = sorted(known - set(found) - {old for _root, old in moved})
    helpers = helper_names(table, directory)
    shown_helpers = sorted((helpers & set(found)) | {root for root, old in moved if old in helpers})
    not_helpers = [found[key] for key in shown_helpers if found[key]["form"] != tool.PROGRAM_HELPER]
    problems = [
        *([_new_text(directory, new, found)] if new else []),
        *([_stale_text(directory, stale)] if stale else []),
        *([_not_helper_text(directory, not_helpers)] if not_helpers else []),
    ]
    assert not problems, "\n".join(problems)


def _added_row_problems(
    row: AddedName, table: KnownTable, listed: set[tuple[str, str, str]]
) -> list[str]:
    """「足した名」の 1 行の誤り: 欄・package・訳・決め・組・「名」の組との重なり。"""
    where = f"足した名 {row.get('package')} {row.get('module')}.{row.get('name')}"
    if not ADDED_FIELDS <= set(row) <= ADDED_FIELDS | ADDED_OPTIONAL_FIELDS:
        return [f"{where}: 欄は package・module・name・訳・決め(と、許した物なら組)"]
    checks = (
        (
            "組" in row and row["組"] not in ALLOWED_GROUPS,
            f"組 {row.get('組')!r} は {ALLOWED_GROUPS} のどれでもない",
        ),
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
            f"{package_dir}: 許した物の組 {group!r} は {ALLOWED_GROUPS} のどれでもない(許す組は目録で形を確かめる)"
            for package_dir, entry in entries
            for group in entry["名"]
            if group.startswith("許した物 — ") and group not in ALLOWED_GROUPS
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


def grown_names(
    first: KnownTable, current: KnownTable, first_submodules: KnownTable | None = None
) -> list[tuple[str, str, str]]:
    """最初の版の「名」の組に無いのに、今の「名」の組に在る名(表が増えた所)。

    公開の submodule の行は、submodule の行を表に足した版(first_submodules)と比べる。その版がまだ無い間(submodule の行を
    足す commit の前)は比べない。
    """
    listed = listed_names(current)
    names = {row for row in listed if row[1] != SUBMODULE} - listed_names(first)
    submodules = (
        {row for row in listed if row[1] == SUBMODULE} - listed_names(first_submodules)
        if first_submodules is not None
        else set()
    )
    return sorted(names | submodules)


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


def first_submodule_version() -> KnownTable | None:
    """公開の submodule の行を既知の表に足した commit の版(行の欄の文字列が履歴で最初に現れた版)。まだ無ければ None。"""
    relative = TABLE.relative_to(ROOT).as_posix()
    marker = json.dumps(SUBMODULE)
    added = _git("log", "--format=%H", "-S", marker, "--", relative).split()
    if not added:
        committed = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{relative}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        assert marker not in committed, (
            f"{relative} の commit 済みの版に submodule の行が在るのに、足した commit が履歴に無い — 減るだけの比べの相手が読めない"
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
    grown = grown_names(first, _read_table(), first_submodule_version())
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
    return _fixture_tree(tmp_path, {"__init__.py": source})


def _fixture_tree(tmp_path: Path, files: Mapping[str, str]) -> Path:
    """packages/ の下に package を 1 つ持つ使い捨ての repo(import の根の下の file の名から中身へ)。"""
    package = tmp_path / "packages" / FIXTURE_DIR
    (package / "src" / FIXTURE_ROOT).mkdir(parents=True, exist_ok=True)
    (package / "pyproject.toml").write_text(textwrap.dedent(FIXTURE_PYPROJECT), encoding="utf-8")
    for relative, source in files.items():
        target = package / "src" / FIXTURE_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(source), encoding="utf-8")
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


FACTORY = '''

from doeff.program import ProgramHandler


def ping_handlers() -> ProgramHandler:
    """A handler factory: calling it gives the handler."""
    return ping_handler
'''
NOT_A_FACTORY = '''

from doeff.program import ProgramHandler


def ping_handlers() -> ProgramHandler:
    """Read as a factory by its return type; calling it gives a function without the handler marker."""
    return lambda program: program
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_factory_whose_call_is_not_a_handler_turns_red(tmp_path: Path) -> None:
    """戻りの型で handler を返す関数(工場)と読める名も、呼ぶと印 _doeff_is_handler_fn の無い値を返せば外れ — 赤(名指す)。
    handler を返すように直すと緑で、目録はその名を、呼んで印を確かめた handler と数える(ADR-DOE-API-001 R3)。
    """
    table = _fixture_table({})
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + NOT_A_FACTORY)
    with pytest.raises(
        AssertionError,
        match=r"既知の表に無い[\s\S]*"
        + re.escape(f"{FIXTURE_ROOT}.ping_handlers({tool.NOT_A_FACTORY}・other"),
    ):
        check_package(_read_fixture(repo), table)

    _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + FACTORY)
    package = _read_fixture(repo)
    check_package(package, table)
    [row] = [row for row in package["exports"] if row["name"] == "ping_handlers"]
    assert (row["kind"], row["form"]) == ("handler", "handler を返す関数")
    assert row["reason"].endswith("引数なしで呼ぶと handler を返す")


#: program を返す関数の 4 つの形: effect を出すだけの helper と、helper の形を越える 3 つ(handler の値を当てる・
#: WithHandler で被せる・中の同じ package の関数が handler を当てる)と、effect に答える物。
PROGRAM_FUNCTIONS = '''

from doeff import DoExpr, Resume, WithHandlerType, do


@do
def pinging():
    """A program helper: its program only emits the effect."""
    return (yield PingEffect())


@do
def pinging_handled():
    """Past a helper: applies a handler to the program it builds."""
    return (yield ping_handler(pinging()))


@do
def pinging_with_handler():
    """Past a helper: installs a handler with WithHandler."""
    return (yield WithHandlerType(ping_handler, pinging()))


def _install(program):
    return ping_handler(program)


def pinging_wrapped() -> DoExpr:
    """Past a helper: the function of its package it calls installs the handler."""
    return _install(pinging())


@do
def answering(effect, cont):
    """Past a helper: answers an effect (Resume is handler control)."""
    return (yield Resume(cont, 1))
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_program_helper_can_be_put_into_the_helper_group(tmp_path: Path) -> None:
    """組「許した物 — program の helper」に置けるのは、目録の形が program の helper の名だけ(ADR-DOE-API-001 R18)。

    effect を出すだけの program の関数は、組に置けば緑・表に無ければ外れのまま赤。handler を被せる関数(handler の値を当てる・
    WithHandler・中の同じ package の関数が当てる)・effect に答える関数・program を返さない関数を組に置くと赤(名と形を名指す)。
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + PROGRAM_FUNCTIONS + OUTLIER)
    package = _read_fixture(repo)
    forms = {row["name"]: row["form"] for row in package["exports"]}
    assert forms["pinging"] == tool.PROGRAM_HELPER
    rest = ["answering", "helper", "pinging_handled", "pinging_with_handler", "pinging_wrapped"]

    def table(helpers: list[str]) -> KnownTable:
        listed = sorted(set(rest) - set(helpers))
        groups = {
            **({HELPER_GROUP: {FIXTURE_ROOT: sorted(helpers)}} if helpers else {}),
            **({"外れ — 内側の module へ移す": {FIXTURE_ROOT: listed}} if listed else {}),
        }
        return {"packages": {FIXTURE_DIR: {"範囲": "決まりの範囲", "名": groups}}, "足した名": []}

    check_package(package, table(["pinging"]))
    with pytest.raises(AssertionError, match=r"既知の表に無い[\s\S]*doeff_fixture_api\.pinging"):
        check_package(package, table([]))

    past_a_helper = [
        ("pinging_handled", tool.HELPER_INSTALLS, "pinging_handled が ping_handler を読む"),
        (
            "pinging_with_handler",
            tool.HELPER_INSTALLS,
            "pinging_with_handler が WithHandlerType を読む",
        ),
        ("pinging_wrapped", tool.HELPER_INSTALLS, "_install が ping_handler を読む"),
        ("answering", tool.HELPER_ANSWERS, "answering が Resume を読む"),
        ("helper", "関数", "戻りの型 int"),
    ]
    for name, form, said in past_a_helper:
        with pytest.raises(AssertionError, match=re.escape(HELPER_GROUP)) as failure:
            check_package(package, table(["pinging", name]))
        assert f"{FIXTURE_ROOT}.{name}({form}・" in str(failure.value)
        assert said in str(failure.value)


def test_an_allowed_group_without_a_check_is_not_well_formed() -> None:
    """許した物の組は、目録で形を確かめる組(ALLOWED_GROUPS)だけを置ける — 確かめの無い組で名を許すと表の形の検が赤。"""
    table = _fixture_table({})
    table["packages"][FIXTURE_DIR]["名"] = {"許した物 — 便利な関数": {FIXTURE_ROOT: ["helper"]}}
    assert shape_problems(table, [FIXTURE_DIR]) == [
        f"{FIXTURE_DIR}: 許した物の組 '許した物 — 便利な関数' は {ALLOWED_GROUPS} のどれでもない(許す組は目録で形を確かめる)"
    ]
    table["packages"][FIXTURE_DIR]["名"] = {HELPER_GROUP: {FIXTURE_ROOT: ["helper"]}}
    assert shape_problems(table, [FIXTURE_DIR]) == []


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_helper_added_after_the_table_is_checked_as_one(tmp_path: Path) -> None:
    """表を足した後に見せる helper は「足した名」に欄「組」つきで書き、組の名と同じ確かめを受ける(ADR-DOE-API-001 R18)。

    helper の形の名は緑・handler を被せる関数を組つきで書くと赤(名と形を名指す)。組を許した物の組の外の名にすると表の形の検が赤。
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + PROGRAM_FUNCTIONS)
    package = _read_fixture(repo)
    rest = ["answering", "pinging_with_handler", "pinging_wrapped"]

    def table(added: str, other: str) -> KnownTable:
        row: AddedName = {
            "package": FIXTURE_DIR,
            "module": FIXTURE_ROOT,
            "name": added,
            "訳": "表を足した後に見せる helper(例)",
            "決め": "ki-f20b18c2aad9",
            "組": HELPER_GROUP,
        }
        return _fixture_table({FIXTURE_ROOT: sorted([*rest, other])}, [row])

    check_package(package, table("pinging", "pinging_handled"))
    assert shape_problems(table("pinging", "pinging_handled"), [FIXTURE_DIR]) == []
    with pytest.raises(AssertionError, match=re.escape(HELPER_GROUP)) as failure:
        check_package(package, table("pinging_handled", "pinging"))
    assert f"{FIXTURE_ROOT}.pinging_handled({tool.HELPER_INSTALLS}・" in str(failure.value)

    unchecked = table("pinging", "pinging_handled")
    unchecked["足した名"][0]["組"] = "許した物 — 便利な関数"
    assert shape_problems(unchecked, [FIXTURE_DIR]) == [
        f"足した名 {FIXTURE_DIR} {FIXTURE_ROOT}.pinging: 組 '許した物 — 便利な関数' は {ALLOWED_GROUPS} のどれでもない"
    ]


def test_a_name_put_into_the_listed_groups_turns_red() -> None:
    """最初の版の「名」の組に無い名を「名」の組に足すと、減るだけの検が名指す(消すのは緑)。"""
    first = _fixture_table({FIXTURE_ROOT: ["a", "b"]})
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a"]})) == []
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a", "b", "c"]})) == [
        (FIXTURE_DIR, FIXTURE_ROOT, "c")
    ]


def test_a_submodule_row_put_into_the_table_after_it_had_submodule_rows_turns_red() -> None:
    """submodule の行は、submodule の行を足した版から減るだけ(最初の版に無くても、その版に在れば増えたと読まない)。"""
    first = _fixture_table({FIXTURE_ROOT: ["a"]})
    with_submodules = _fixture_table({FIXTURE_ROOT: ["a"], SUBMODULE: [f"{FIXTURE_ROOT}.x"]})
    assert grown_names(first, with_submodules, with_submodules) == []
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a"]}), with_submodules) == []
    more = _fixture_table(
        {FIXTURE_ROOT: ["a"], SUBMODULE: [f"{FIXTURE_ROOT}.x", f"{FIXTURE_ROOT}.y"]}
    )
    assert grown_names(first, more, with_submodules) == [
        (FIXTURE_DIR, SUBMODULE, f"{FIXTURE_ROOT}.y")
    ]


# --- 失敗ケース: 公開の submodule と、名を submodule から入口へ移す直し(ADR-DOE-API-001 R5) ---

ANOTHER_EFFECT = '''
from doeff import EffectBase


class PongEffect(EffectBase):
    """An effect in a submodule: the names are fine, the public submodule is not."""
'''
#: 入口へ移す前の submodule: R2 の型(結果の型)と定数(入口へ移す)と、外れの関数(_ の中に残す)。
TYPES = '''
from dataclasses import dataclass


@dataclass(frozen=True)
class PingResult:
    """The answer to a ping (a result type, shown from the root)."""

    count: int


LIMIT = 3


def helper(x: int) -> int:
    """An outlier: stays inside the hidden module."""
    return x + 1
'''
#: 入口が _types の名を出す 2 つの形: すぐ import する形と、遅延の import の形(__getattr__・__dir__・TYPE_CHECKING)。
ROOT_SHOWS_TYPES = {
    "eager": f"""
from {FIXTURE_ROOT}._types import LIMIT as LIMIT, PingResult as PingResult
""",
    "lazy": f"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from {FIXTURE_ROOT}._types import LIMIT as LIMIT, PingResult as PingResult

_LAZY = ("LIMIT", "PingResult")


def __getattr__(name):
    if name in _LAZY:
        from {FIXTURE_ROOT} import _types

        return _types.__dict__[name]
    raise AttributeError(name)


def __dir__():
    return sorted([*globals(), *_LAZY])
""",
}
#: 元の path に残す移し替えの module の 2 つの形: * で名を写す形と、sys.modules の項を _types に替える形。
SHIM = {
    "star": f"""
from {FIXTURE_ROOT}._types import *
""",
    "alias": f"""
import sys

from {FIXTURE_ROOT} import _types

sys.modules[__name__] = _types
""",
}
#: 入口が自分で定義する、同じ名の違う型(移した名ではない)。
ANOTHER_PING_RESULT = '''


class PingResult:
    """A different definition with the known name."""
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_added_public_submodule_turns_red_and_hiding_it_turns_green(tmp_path: Path) -> None:
    """_ の無い submodule を足すと、出す名が effect だけでも赤(submodule を名指す)・名に _ を付けて隠すと緑。"""
    table = _fixture_table({})
    check_package(_read_fixture(_fixture_repo(tmp_path / "root", EFFECTS_AND_HANDLERS)), table)

    public = _fixture_tree(
        tmp_path / "public", {"__init__.py": EFFECTS_AND_HANDLERS, "extra.py": ANOTHER_EFFECT}
    )
    with pytest.raises(
        AssertionError, match=r"既知の表に無い[\s\S]*doeff_fixture_api\.extra\(公開の submodule\)"
    ):
        check_package(_read_fixture(public), table)

    hidden = _fixture_tree(
        tmp_path / "hidden", {"__init__.py": EFFECTS_AND_HANDLERS, "_extra.py": ANOTHER_EFFECT}
    )
    check_package(_read_fixture(hidden), table)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize(("root_form", "shim_form"), [("eager", "star"), ("lazy", "alias")])
def test_moving_names_from_a_submodule_to_the_root_keeps_the_table_green(
    tmp_path: Path, root_form: str, shim_form: str
) -> None:
    """名を submodule から入口へ移しても、表の module を書き換えずに緑(stage 1 — 移し替えの module を残す間)。

    stage 3 で移し替えの module を消すと、表に残るのは隠した submodule と _ の中に残した外れの名だけで、それを表から消すと緑
    (入口へ移した名は表の submodule の行のまま読む)。
    """
    submodule = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [submodule], submodule: ["LIMIT", "PingResult", "helper"]})
    before = _fixture_tree(
        tmp_path / "before", {"__init__.py": EFFECTS_AND_HANDLERS, "types.py": TYPES}
    )
    check_package(_read_fixture(before), table)

    root = EFFECTS_AND_HANDLERS + ROOT_SHOWS_TYPES[root_form]
    stage_1 = _fixture_tree(
        tmp_path / "stage-1", {"__init__.py": root, "_types.py": TYPES, "types.py": SHIM[shim_form]}
    )
    check_package(_read_fixture(stage_1), table)

    stage_3 = _read_fixture(
        _fixture_tree(tmp_path / "stage-3", {"__init__.py": root, "_types.py": TYPES})
    )
    with pytest.raises(AssertionError, match=r"目録に無い名が 2 個") as failure:
        check_package(stage_3, table)
    assert f"{submodule}(公開の submodule)" in str(failure.value)
    assert f"{submodule}.helper" in str(failure.value)
    check_package(stage_3, _fixture_table({submodule: ["LIMIT", "PingResult"]}))


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_different_definition_with_a_known_name_at_the_root_turns_red(tmp_path: Path) -> None:
    """submodule が名を出している間に、同じ名の違う定義を入口に足すと赤(移した名と読まない)。"""
    submodule = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [submodule], submodule: ["LIMIT", "PingResult", "helper"]})
    repo = _fixture_tree(
        tmp_path, {"__init__.py": EFFECTS_AND_HANDLERS + ANOTHER_PING_RESULT, "types.py": TYPES}
    )
    with pytest.raises(AssertionError, match=r"既知の表に無い[\s\S]*doeff_fixture_api\.PingResult"):
        check_package(_read_fixture(repo), table)


#: 子の package を _types へ移した後に元の path へ残す移し替えの module(card ki-299be2ea4a13 の道具の形 — 1 行目の印つき)。
MARKED_SHIM = f"""# moved-submodule-shim: {FIXTURE_ROOT}.types -> {FIXTURE_ROOT}._types
import sys
from importlib import import_module

sys.modules[__name__] = import_module("{FIXTURE_ROOT}._types")
"""
#: 子の package の下の module(外れの関数を 1 つ出す)。
BELOW = '''
def sub_helper(x: int) -> int:
    """An outlier in a module below the child package."""
    return x - 1
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_marked_shim_keeps_the_modules_below_a_moved_package_at_their_old_names(
    tmp_path: Path,
) -> None:
    """子の package を _ へ移し、1 行目に印のある移し替えの module を残す間は、下の module も古い名のまま数えて緑。

    印の無い移し替えの module では、下の module は目録から消える(古い名の submodule と名の行が、表に在るのに目録に無い)。
    """
    child, below = f"{FIXTURE_ROOT}.types", f"{FIXTURE_ROOT}.types.sub"
    table = _fixture_table(
        {SUBMODULE: [child, below], child: ["LIMIT", "PingResult", "helper"], below: ["sub_helper"]}
    )
    before = _fixture_tree(
        tmp_path / "before",
        {"__init__.py": EFFECTS_AND_HANDLERS, "types/__init__.py": TYPES, "types/sub.py": BELOW},
    )
    check_package(_read_fixture(before), table)

    moved = {
        "__init__.py": EFFECTS_AND_HANDLERS,
        "_types/__init__.py": TYPES,
        "_types/sub.py": BELOW,
    }
    stage_1 = _fixture_tree(tmp_path / "stage-1", {**moved, "types.py": MARKED_SHIM})
    check_package(_read_fixture(stage_1), table)

    unmarked = MARKED_SHIM.split("\n", 1)[1]
    without_mark = _fixture_tree(tmp_path / "unmarked", {**moved, "types.py": unmarked})
    with pytest.raises(AssertionError, match=r"目録に無い名が 2 個") as failure:
        check_package(_read_fixture(without_mark), table)
    assert f"{below}(公開の submodule)" in str(failure.value)
    assert f"{below}.sub_helper" in str(failure.value)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_marked_shim_of_a_namespace_dir_keeps_the_modules_below_at_their_old_names(
    tmp_path: Path,
) -> None:
    """__init__ の無い子の dir を _ へ移し、印のある移し替えの module を残す間も、下の module を古い名で数えて緑。

    移す前の namespace の dir は module でなく(submodule の行が無い)、目録は dir を通り抜けて下の module を数える。
    移し替えの module はその dir の代わりなので、自分は submodule と数えずに、移した先の dir を同じに通り抜ける
    (doeff-cluster の区画 sim を _sim へ移した時、移した先を辿らずに下の 603 行が消え、移し替えの module が表に無い
    submodule になった — card ki-9b8308f2c7c3)。
    """
    below = f"{FIXTURE_ROOT}.types.sub"
    table = _fixture_table({SUBMODULE: [below], below: ["sub_helper"]})
    before = _fixture_tree(
        tmp_path / "before", {"__init__.py": EFFECTS_AND_HANDLERS, "types/sub.py": BELOW}
    )
    check_package(_read_fixture(before), table)

    stage_1 = _fixture_tree(
        tmp_path / "stage-1",
        {"__init__.py": EFFECTS_AND_HANDLERS, "_types/sub.py": BELOW, "types.py": MARKED_SHIM},
    )
    check_package(_read_fixture(stage_1), table)


#: import すると落ちる子の module(目録は名を text から読む — doeff-ml-nexus の import できない Hy の module の様に)。
UNIMPORTABLE = '''
raise RuntimeError("this module cannot be imported")


def helper(x: int) -> int:
    """An outlier, read from the text of a module that cannot be imported."""
    return x + 1
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_marked_shim_of_a_module_that_cannot_be_imported_keeps_the_names_of_the_moved_text(
    tmp_path: Path,
) -> None:
    """import できない子を _ へ移し、印のある移し替えの module を残す間も、名を移した先の text から古い名で数えて緑。

    移し替えの module の text は名を定義しないので、移した先を読まないと名が目録から消え、表に在るのに目録に無い名で赤になる
    (doeff-ml-nexus の import できない Hy の module で起きた — card ki-460c44bf9b79)。
    """
    child = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [child], child: ["helper"]})
    before = _fixture_tree(
        tmp_path / "before", {"__init__.py": EFFECTS_AND_HANDLERS, "types.py": UNIMPORTABLE}
    )
    check_package(_read_fixture(before), table)

    stage_1 = _fixture_tree(
        tmp_path / "stage-1",
        {"__init__.py": EFFECTS_AND_HANDLERS, "_types.py": UNIMPORTABLE, "types.py": MARKED_SHIM},
    )
    check_package(_read_fixture(stage_1), table)
