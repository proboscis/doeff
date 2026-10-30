"""subpackage の公開 API に effect と handler の外の名を足すと赤になり、今の外れは減るだけの既知の表に並ぶ事。

決まり: doeff の subpackage が利用者に見せる公開 API は effect と handler だけ(取り組み doeff-subpackages-effects-only)。
公開 API を数えるのは tools/public_api_inventory.py の 1 か所で、この file は数えない — 道具の目録の行のうち、種類が
effect でも handler でもない物(それ以外・未分類)を外れとし、tests/data/public_api_known_violations.json の既知の表
(違反の表 — 外れと境界だけ)と tests/data/public_api_allowed.json の許した物の表(決まりが名ごとに許した名)の 2 つと
(名を出す module, 名) の鍵で突き合わせる。_ で始まらない submodule そのものも、目録の 1 行(名を出す module は
(submodule)・名は submodule の import の名)の外れと数える(ADR-DOE-API-001 R5 — 公開の名は import の根からだけ出す):

1. package ごとに 1 件(packages/ の下の dir の名で引く): どちらの表にも無い外れが在れば赤(新しく足した外れ・公開の
   submodule)、表に在るのに目録に無い名が在れば赤(直したのに表を縮め忘れた — 2 つの表のどちらでも)。
2. 名を submodule から入口へ移した行は、表の module を書き換えずに同じ行と読む: 表に無い根の (根, 名) は、表に同じ名の
   (根の下の submodule, 名) が在り、その submodule がまだ名を出していれば定義の場所が同じ時だけ、もう出していなければ
   (古い口を閉じた — 定義を比べる相手が無い)名と根だけで、表のその行と読む。違う定義を根に足すのは赤のまま。
3. 表の形: 目録の package(packages/ の下の dir のうち、doeff の中の部品の外)の全部が 1 つずつ載り、範囲が決まった値で、名が
   組の中で並んで重ならない。違反の表は許した物の組を持たない(許した物は許した物の表だけに置くので、違反の表は空になれる)。
4. 違反の表は減るだけ: 表の「名」の組の名は、この表を足した commit の版(git の履歴の最初の版)に在る名に限る。submodule
   の行は、submodule の行を表に足した commit の版に在る物に限る。新しく足すしか無い外れは「足した名」に、訳と決め
   (card・依頼・利用者の発言の id)を 1 行ずつ書く。
5. 失敗ケース: 使い捨ての package を作り、effect と handler だけなら緑・外れを 1 つ足すと赤・外すと緑・直したのに表に
   残すと赤・訳と決めつきで「足した名」に書くと緑・_ の無い submodule を足すと赤・名を submodule から入口へ移しても緑
   (移し替えの module を残す間も消した後も)・同じ名の違う定義を入口に足すと赤・1 行目に印 moved-submodule-shim の在る
   移し替えの module は、移した先の package の下の module も古い名で数えて緑になる事を、同じ照合で確かめる。
   印の下に名の固定の列を持つ移し替えの module は、移した先の _ の module に helper を足しても目録の行が増えず緑・列が
   無ければ赤・入口に名を足すと列が在っても赤(card ki-4fbed45b5863)。移し替えの module の全部が列を持つ事も確かめる。
   戻りの型で handler を返す関数(工場)と読める名が、呼ぶと印 _doeff_is_handler_fn の無い値を返すと赤・handler を返すように
   直すと緑(目録は引数なしで呼べる工場を呼んで印を確かめる — ADR-DOE-API-001 R3)。
6. 許した物の表は決めの id つきで足せる(減るだけでない): 1 行 = (package, module, 名) と組と決め。決めが card・依頼・
   発言の id でない行・違反の表と同じ鍵の行は赤。組は目録で形を確かめる組(ALLOWED_GROUPS)だけを置ける。組「許した物 —
   program の helper」(ADR-DOE-API-001 R18 — 決まりが名ごとに許した、effect を出すだけの program を返す関数)に置けるのは、
   目録の形が program の helper の名だけ: handler を被せる・effect に答える・program を返さない関数を置くと赤。組「許した物 —
   Hy の macro」(R14・R15・R31 — macro を出す module は公開の境界に置く)に置けるのは、目録の形が Hy の macro の名と、Hy の
   macro だけを出す公開の submodule(macro の module)の行だけ: macro でない名・macro の外の名も出す submodule を置くと赤。
   組「許した物 — CLI コマンド」(R7・R31)に置けるのは console script と Rust の実行 file の行だけ。組「許した物 — 換算の
   関数」(R35 — effect の答えを他の物差しへ換える関数)に置けるのは、目録の形が換算の関数の名だけ: 中で effect・handler・
   program を作る・時計や環境や file を読む・generator の関数を置くと赤。
7. 決まりの範囲の、Rust の拡張を持つ package(maturin)は、目録が import で読めなければ赤 — 字面の読みは入口が拡張から
   出す名を数えないので(card ki-1e51e218527e — doeff-indexer の Indexer・SymbolInfo を数えていなかった)、環境に入れて読む。
8. doeff の中の部品(目録の道具の DOEFF_PARTS — doeff-vm)は、doeff の根の package と同じく目録が数えず、表に載せない
   (ADR-DOE-API-001 R11・card ki-9c326052f21c)。その前提として、doeff の外の file(packages/ の下の部品の外の package・examples・
   docs/adr)が部品の import の根を import すれば赤(file と行と module を名指す)。失敗ケース: 使い捨ての repo で、素の import・
   from・下の module・Hy の import と require・importlib と pytest.importorskip に渡す名の文字列は赤、doeff の根と doeff._vm から
   読む形と、部品そのもの・doeff の根の package・doeff の検(tests/)の import は緑。
9. 入口(import の根の __init__)の出す名と同じ名の子(移し替えの module を含む)を根の下に置かない(ADR-DOE-API-001 R34・
   card ki-db44e4d3efe4 — 古い path の import が親の属性を module に束ね、入口の関数を隠して記録の service が起動の度に
   落ちた)。package ごとに 2 つで照らす: 字面の照合(入口の file が束ねる名と子の名)と、新しい process で根と全部の公開の
   module(古い path を含む)を 1 つずつ import した後も入口の名が module に替わらない確かめ。失敗ケース: 使い捨ての package
   の入口が関数 serve(遅延)・tool(すぐ)を出す所に同じ名の移し替えの module を置くと両方が赤・殻を消すと緑。
   根の下の package(_ の package を含む)の入口にも同じく当てる(card ki-d3a096ce3eaa — 移した package の古い path
   doeff_agents.effects.agent の import が doeff_agents._effects の関数 agent を module で上書きした)。失敗ケース: 根の下の
   package _inner の入口が同じ名の子 tool から関数 tool を束ね、移した package の古い path inner を殻が答えると両方が赤・
   子を _tool へ移すと緑。
"""

from __future__ import annotations

import ast
import collections
import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, NotRequired, TypedDict, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "tests" / "data" / "public_api_known_violations.json"
ALLOWED = ROOT / "tests" / "data" / "public_api_allowed.json"
#: 目録の種類のうち、決まりの外の物(effect と handler の外)。
OUTSIDE_KINDS = frozenset({"other", "unclassified"})
#: package の範囲。範囲の外の package は名を突き合わせない(決めの id が要る)。
SCOPES = ("決まりの範囲", "範囲の答え待ち", "範囲の外")
#: 違反の表の名の組の名の頭(分け — 直しの向きか境界の種類)。許した物は許した物の表に置く。
GROUP_HEADS = ("外れ — ", "境界 — ", "未分け — ")
#: 許した物の組の名の頭(許した物の表の欄「組」)。
ALLOWED_HEAD = "許した物 — "
#: 決まりが名ごとに許した program の helper の組(ADR-DOE-API-001 R18)— 目録の形が program の helper の名だけを置ける。
HELPER_GROUP = "許した物 — program の helper"
#: 決まりが許した Hy の macro の組(ADR-DOE-API-001 R14・R15・R31)— macro の名と macro の module の行だけを置ける。
MACRO_GROUP = "許した物 — Hy の macro"
#: 決まりが許した CLI コマンドの組(ADR-DOE-API-001 R7・R31)— console script と Rust の実行 file の行だけを置ける。
COMMAND_GROUP = "許した物 — CLI コマンド"
#: 決まりが名ごとに許した換算の関数の組(ADR-DOE-API-001 R35)— 目録の形が換算の関数の名だけを置ける。
CONVERSION_GROUP = "許した物 — 換算の関数"
#: 許した物の組ごとの、置ける物(目録で確かめる形 — 形の合わない名を名指す文に出す)。確かめの無い組は置けない。
ALLOWED_SHAPES: dict[str, str] = {
    HELPER_GROUP: "program を返し、中で handler を被せず effect に答えない関数だけを置ける(ADR-DOE-API-001 R18)",
    MACRO_GROUP: "Hy の macro と、Hy の macro だけを出す公開の submodule(macro の module)だけを置ける"
    "(ADR-DOE-API-001 R14・R15・R31)",
    COMMAND_GROUP: "console script と Rust の実行 file だけを置ける(ADR-DOE-API-001 R7・R31)",
    CONVERSION_GROUP: "値を受けて値を返し、中で effect も handler も program も作らず、時計・環境・file・process・乱数を"
    "読まない関数だけを置ける(ADR-DOE-API-001 R35)",
}
#: 許した物の組の全部(組ごとに、目録で形を確かめる — fits_allowed_group)。
ALLOWED_GROUPS = tuple(ALLOWED_SHAPES)
#: 決めの id: card(ki-)・依頼(lt-)・利用者の発言(ci-)。
DECISION = re.compile(r"(ki-[0-9a-f]{12}|lt-[0-9A-Z]{26}|ci-[0-9A-Z]{26})")
#: 1 package を子の process で読む秒の上限(load 約 55 の下で最も重い doeff-cluster が 9 秒・混みの倍率は根の conftest が掛ける)。
_PROBE_SECONDS = 100

Key = tuple[str, str]


class AddedName(TypedDict):
    """「足した名」の 1 行: 表を足した後に足すしか無かった外れ(訳と決めの id つき)。

    許した物(新しく見せる program の helper など)はここに書かず、許した物の表に決めの id つきで足す。
    """

    package: str
    module: str
    name: str
    訳: str
    決め: str


#: 「足した名」の 1 行の欄。
ADDED_FIELDS = frozenset({"package", "module", "name", "訳", "決め"})


class PackageEntry(TypedDict):
    """1 package の項: 範囲と、組の名(分け — 直しの向きか境界の種類)から module から名の列へ。範囲の外は決めの id を持つ。"""

    範囲: str
    名: dict[str, dict[str, list[str]]]
    決め: NotRequired[str]


class KnownTable(TypedDict):
    """既知の表(違反の表)の全体(説明と問いは読む人のための欄)。"""

    説明: NotRequired[list[str]]
    問い: NotRequired[str]
    packages: dict[str, PackageEntry]
    足した名: list[AddedName]


class AllowedName(TypedDict):
    """許した物の表の 1 行: 決まりが名ごとに許した名(組 = 目録で形を確かめる許す組・決め = 許した決めの id)。"""

    package: str
    module: str
    name: str
    組: str
    決め: str


#: 許した物の表の 1 行の欄。
ALLOWED_FIELDS = frozenset({"package", "module", "name", "組", "決め"})


class AllowedTable(TypedDict):
    """許した物の表の全体(説明は読む人のための欄)。行は (package, module, name) の順に並べ、重ねない。"""

    説明: NotRequired[list[str]]
    許した物: list[AllowedName]


#: 何も許していない許した物の表(使い捨ての package の照合の既定)。
NOTHING_ALLOWED: AllowedTable = {"許した物": []}


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


def _read_allowed(path: Path = ALLOWED) -> AllowedTable:
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


def allowed_names(allowed: AllowedTable, package_dir: str) -> set[Key]:
    """1 package の、許した物の表に置いた (module, 名) の全部。"""
    return {
        (row["module"], row["name"]) for row in allowed["許した物"] if row["package"] == package_dir
    }


def allowed_groups(allowed: AllowedTable, package_dir: str) -> dict[Key, str]:
    """1 package の、許した物の表に置いた (module, 名) から組へ(目録で形を確かめる組の行だけ)。"""
    return {
        (row["module"], row["name"]): row["組"]
        for row in allowed["許した物"]
        if row["package"] == package_dir and row["組"] in ALLOWED_SHAPES
    }


def _is_macro_module(package: Mapping[str, Any], module: str) -> bool:
    """module の目録の名が 1 つ以上在り、全部が Hy の macro(macro の module — ADR-DOE-API-001 R14・R15・R31)。"""
    forms = {row["form"] for row in package["exports"] if row["module"] == module}
    return forms == {tool.MACRO_FORM}


def fits_allowed_group(group: str, row: Mapping[str, Any], package: Mapping[str, Any]) -> bool:
    """目録の行 1 つが許した物の組の形に合う(組ごとの形は ALLOWED_SHAPES の文)。"""
    if group == HELPER_GROUP:
        return row["form"] == tool.PROGRAM_HELPER
    if group == CONVERSION_GROUP:
        return row["form"] == tool.CONVERSION
    if group == MACRO_GROUP:
        return row["form"] == tool.MACRO_FORM or (
            row["module"] == SUBMODULE and _is_macro_module(package, row["name"])
        )
    return group == COMMAND_GROUP and row["form"] in (tool.CONSOLE_SCRIPT, tool.RUST_EXECUTABLE)


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
        f"{directory}: 既知の表と許した物の表に無い、effect と handler の外の公開名が {len(new)} 個 — effect か handler にするか、"
        "名を _ で始めるか _ で始まる module へ移す(公開の submodule は名に _ を付けて隠し、見せる名は入口から出す)。"
        f"決まりが許した名なら許した物の表 {ALLOWED.relative_to(ROOT)} に組と決めつきで、どうしても見せる外れなら"
        "既知の表の「足した名」に訳と決めつきで書く:\n"
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


def _stale_allowed_text(directory: str, stale: list[Key]) -> str:
    return (
        f"{directory}: 許した物の表に在るのに目録に無い名が {len(stale)} 個 — 消した名と隠した名は許した物の表から消す:\n"
        + "\n".join(f"  {_key_text(module, name)}" for module, name in stale)
    )


def _misshapen_text(directory: str, group: str, rows: list[Mapping[str, Any]]) -> str:
    return (
        f"{directory}: 許した物の表の組「{group}」に、目録の形が組に合わない名が {len(rows)} 個 — "
        f"{ALLOWED_SHAPES[group]}。直すか、許した物の表から外して既知の表の外れか境界の組へ戻す:\n"
        + "\n".join(
            f"  {_key_text(row['module'], row['name'])}({row['form']}・{row['reason']})"
            for row in rows
        )
    )


def unread_native_modules(
    package: Mapping[str, Any], entry: PackageEntry, repo: Path
) -> list[Mapping[str, Any]]:
    """決まりの範囲の、Rust の拡張を持つ package の module のうち、目録が import できずに字面で読んだ物。

    字面の読みは source に定義の在る名しか数えないので、入口が拡張から出す名(前の doeff-indexer の Indexer・SymbolInfo)を
    数えない(card ki-1e51e218527e)。決まりの範囲の名は全部を数えるので、そういう package は環境に入れて import で読む。
    """
    if (
        entry["範囲"] != "決まりの範囲"
        or not tool.read_layout(repo / "packages" / package["dir"]).maturin
    ):
        return []
    return [module for module in package["modules"] if module["read"] == "text"]


def _unread_native_text(directory: str, modules: list[Mapping[str, Any]]) -> str:
    return (
        f"{directory}: Rust の拡張を持つ package の module を目録が import で読めず、字面で読んだ — 字面では入口が拡張から"
        "出す名を数えられない。root の pyproject の dev の組に package を入れ、make sync の環境で読む:\n"
        + "\n".join(f"  {module['name']}: {module['error']}" for module in modules)
    )


def _diff_against_tables(
    package: Mapping[str, Any], table: KnownTable, allowed: AllowedTable
) -> tuple[dict[Key, Mapping[str, Any]], set[Key], set[Key], set[tuple[Key, Key]], list[Key]]:
    """目録の 1 package の外れと、2 つの表の名・移した行・どちらの表にも無い新しい外れを返す(check_package と
    tools/public_api_blame.py が同じ照合を使う 1 点)。"""
    directory = package["dir"]
    found = outside_rows(package)
    known = known_names(table, directory)
    permitted = allowed_names(allowed, directory)
    moved = moved_to_root(package["import_roots"], found, known | permitted)
    new = sorted(set(found) - known - permitted - {root for root, _old in moved})
    return found, known, permitted, moved, new


def unlisted_names(package: Mapping[str, Any], table: KnownTable, allowed: AllowedTable) -> list[Key]:
    """表に書かずに足された外れ((module, 名)の列)— 日次の全体検証の後段 tools/public_api_blame.py が、足した commit を引くために読む。"""
    entry = table["packages"].get(package["dir"])
    if package["error"] is not None or entry is None or entry["範囲"] == "範囲の外":
        return []
    return _diff_against_tables(package, table, allowed)[4]


def check_package(
    package: Mapping[str, Any],
    table: KnownTable,
    allowed: AllowedTable = NOTHING_ALLOWED,
    repo: Path = ROOT,
) -> None:
    """目録の 1 package を既知の表(違反の表)と許した物の表と突き合わせる。どちらの表にも無い外れと、表に在るのに目録に
    無い名(表ごと)を名指して止める。

    名を submodule から入口へ移した行(moved_to_root)は、表の submodule の行と同じ行と読む。許した物の表の名は、目録の形が
    置いた組の形に合わなければ組ごとに名指して止める(fits_allowed_group — 入口へ移した行は入口の行の形で見る)。
    決まりの範囲の Rust の拡張を持つ package は、目録が import で読めなければ止める(unread_native_modules)。
    """
    directory = package["dir"]
    assert package["error"] is None, f"{directory} を読めなかった: {package['error']}"
    entry = table["packages"].get(directory)
    assert entry is not None, (
        f"{directory} が既知の表 {TABLE.relative_to(ROOT)} に無い — package を足したら範囲と名の組を表に足す"
    )
    if entry["範囲"] == "範囲の外":
        return
    found, known, permitted, moved, new = _diff_against_tables(package, table, allowed)
    olds = {old for _root, old in moved}
    stale = sorted(known - set(found) - olds)
    stale_allowed = sorted(permitted - set(found) - olds)
    groups = allowed_groups(allowed, directory)
    shown = {key: groups[key] for key in groups.keys() & found.keys()} | {
        root: groups[old] for root, old in moved if old in groups
    }
    misshapen = {
        group: [
            found[key]
            for key in sorted(shown)
            if shown[key] == group and not fits_allowed_group(group, found[key], package)
        ]
        for group in ALLOWED_GROUPS
    }
    unread = unread_native_modules(package, entry, repo)
    problems = [
        *([_unread_native_text(directory, unread)] if unread else []),
        *([_new_text(directory, new, found)] if new else []),
        *([_stale_text(directory, stale)] if stale else []),
        *([_stale_allowed_text(directory, stale_allowed)] if stale_allowed else []),
        *(_misshapen_text(directory, group, rows) for group, rows in misshapen.items() if rows),
    ]
    assert not problems, "\n".join(problems)


def _added_row_problems(
    row: AddedName, table: KnownTable, listed: set[tuple[str, str, str]]
) -> list[str]:
    """「足した名」の 1 行の誤り: 欄・package・訳・決め・「名」の組との重なり。"""
    where = f"足した名 {row.get('package')} {row.get('module')}.{row.get('name')}"
    if set(row) != ADDED_FIELDS:
        return [
            f"{where}: 欄は package・module・name・訳・決め(許した物は許した物の表 {ALLOWED.relative_to(ROOT)} に置く)"
        ]
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
            f"{package_dir}: 許した物の組 {group!r} は既知の表に置かない — 許した物の表 {ALLOWED.relative_to(ROOT)} に"
            "組と決めつきで置く(既知の表は外れと境界だけを持つ)"
            if group.startswith(ALLOWED_HEAD)
            else f"{package_dir}: 組の名 {group!r} が {GROUP_HEADS} のどれでも始まらない"
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


def _allowed_row_problems(
    row: AllowedName, table: KnownTable, violations: set[tuple[str, str, str]]
) -> list[str]:
    """許した物の表の 1 行の誤り: 欄・組・決め・package・既知の表との重なり。"""
    where = f"許した物 {row.get('package')} {row.get('module')}.{row.get('name')}"
    if set(row) != ALLOWED_FIELDS:
        return [f"{where}: 欄は package・module・name・組・決め(決めの id の無い行は置けない)"]
    entry = table["packages"].get(row["package"])
    checks = (
        (
            row["組"] not in ALLOWED_GROUPS,
            f"組 {row['組']!r} は {ALLOWED_GROUPS} のどれでもない(許す組は目録で形を確かめる)",
        ),
        (
            DECISION.fullmatch(row["決め"]) is None,
            f"決め {row['決め']!r} が card(ki-)・依頼(lt-)・発言(ci-)の id でない",
        ),
        (entry is None, "package が既知の表に無い"),
        (
            entry is not None and entry["範囲"] == "範囲の外",
            "範囲の外の package の名は許さない(名を突き合わせない)",
        ),
        (
            (row["package"], row["module"], row["name"]) in violations,
            "既知の表にも在る(1 つの名は 1 つの表だけに置く)",
        ),
    )
    return [f"{where}: {text}" for failed, text in checks if failed]


def allowed_problems(allowed: AllowedTable, table: KnownTable) -> list[str]:
    """許した物の表の形の誤り: 行ごとの欄・組・決め・package・既知の表との重なりと、行の並びと重なり。"""
    rows = allowed["許した物"]
    violations = listed_names(table) | {
        (row["package"], row["module"], row["name"]) for row in table["足した名"]
    }
    keys = [(row.get("package", ""), row.get("module", ""), row.get("name", "")) for row in rows]
    return [
        *(problem for row in rows for problem in _allowed_row_problems(row, table, violations)),
        *(
            ["許した物の表の行は (package, module, name) の順に並べ、重ねない"]
            if keys != sorted(set(keys))
            else []
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
    """package の公開名のうち effect と handler の外の物が、既知の表と許した物の表の名とちょうど同じ(足しても直しても
    表と合わせる)。"""
    [package] = tool.inventory(ROOT, [package_dir], jobs=1, timeout=_PROBE_SECONDS)
    check_package(package, _read_table(), _read_allowed())


def test_the_known_table_has_one_well_formed_entry_per_package_dir() -> None:
    """表は packages/ の下の dir を 1 つずつ持ち、範囲・組の名・名の並び・「足した名」の訳と決めが形どおり。許した物の組を
    持たない。"""
    problems = shape_problems(_read_table(), (path.name for path in tool.package_dirs(ROOT)))
    assert not problems, "\n".join(problems)


def test_the_allowed_table_is_well_formed() -> None:
    """許した物の表の行は、組が形を確かめる組・決めが id・package が既知の表の範囲の内で、鍵の順に並び、既知の表と重ならない。"""
    problems = allowed_problems(_read_allowed(), _read_table())
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


# --- 入口の名と同じ名の submodule(ADR-DOE-API-001 R34・card ki-db44e4d3efe4) ---

#: 子の module の file の末尾(拡張の module は名の後の最初の . までが名)。
_CHILD_SUFFIXES = (".py", ".pyi", ".hy")
_EXTENSION_SUFFIXES = (".so", ".pyd")


def _bound_by_entry(node: ast.stmt, root: str) -> Iterator[tuple[str, int]]:
    """入口の top level の文 1 つが束ねる名と行(子の module そのものを束ねる import と ``*`` は数えない)。"""
    # match の文で書かない: commit の hook の semgrep が match の文を構文として読めない(tools/hide_submodules.py と同じ)。
    if isinstance(node, (ast.If, ast.Try)):
        handlers = node.handlers if isinstance(node, ast.Try) else []
        finals = node.finalbody if isinstance(node, ast.Try) else []
        for inner in [*node.body, *(s for h in handlers for s in h.body), *node.orelse, *finals]:
            yield from _bound_by_entry(inner, root)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name, node.lineno
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "_LAZY_EXPORTS" and isinstance(node.value, ast.Dict):
                yield from (
                    (key.value, key.lineno)
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
            else:
                yield target.id, node.lineno
    elif isinstance(node, ast.ImportFrom):
        own = (node.level == 1 and node.module is None) or (node.level == 0 and node.module == root)
        for alias in node.names:
            name = alias.asname or alias.name
            if alias.name != "*" and not (own and alias.name in (name, f"_{name}")):
                yield name, node.lineno


def _is_child(path: Path) -> bool:
    """package のすぐ下の、_ で始まらない子(module の file・package の dir・名前空間の dir)か。"""
    if path.name.startswith(("_", ".")):
        return False
    if path.is_dir():
        return any(any(path.glob(f"*{suffix}")) for suffix in _CHILD_SUFFIXES)
    return path.name.endswith(_CHILD_SUFFIXES + _EXTENSION_SUFFIXES)


def _child_names(package: Path) -> dict[str, Path]:
    """package のすぐ下の子の名から path へ(同じ名の .py と .pyi は名の順で先の file)。"""
    return {
        path.name.split(".")[0]: path
        for path in sorted(package.iterdir(), reverse=True)
        if _is_child(path)
    }


#: package の dir にする入口の file(Hy の入口も import できる — 新しい process の確かめが import する)。
_ENTRY_FILES = ("__init__.py", "__init__.hy")


def entry_packages(root_dir: Path, root: str) -> list[tuple[str, Path]]:
    """import の根と、その下の package(_ の package を含む)の名と dir — 入口の file を持つ dir(名の順)。

    根の下の package の入口も根と同じく名を出し、移した package の古い path の import がその package の属性を同じ名の子の
    module で上書きする(doeff_agents.effects.agent の import が doeff_agents._effects の agent を — card ki-d3a096ce3eaa)。
    """
    if not root_dir.is_dir():
        return []
    dirs = [root_dir, *(path for path in root_dir.rglob("*") if path.is_dir())]
    return sorted(
        (".".join([root, *relative.parts]), path)
        for path in dirs
        for relative in [path.relative_to(root_dir)]
        if all(part.isidentifier() and part != "__pycache__" for part in relative.parts)
        and any((path / name).is_file() for name in _ENTRY_FILES)
    )


def written_entry_name_clashes(package_dir: Path) -> list[str]:
    """字面の照合: import の根と、その下の package(_ の package を含む)の ``__init__.py`` が束ねる公開の名と同じ名の子
    (移し替えの module を含む)。

    import の順に依らない — 移し替えの module が package の class を差し替えて束ねを捨てても(前の hide の手当て)赤。
    """
    repo = package_dir.parent.parent
    entries = [
        (name, path, path / "__init__.py")
        for root in tool.read_layout(package_dir).roots
        for name, path in entry_packages(root.path, root.name)
        if (path / "__init__.py").is_file()
    ]
    return [
        f"{package}.{name}: 入口 {init.relative_to(repo)} の {bound[name]} 行が出す名と同じ名の子"
        f" {child.relative_to(repo)} が在る"
        for package, path, init in entries
        for bound in [_entry_bindings(init, package)]
        for name, child in sorted(_child_names(path).items())
        if name in bound
    ]


def _entry_bindings(init: Path, root: str) -> dict[str, int]:
    """入口の file が top level で束ねる公開の名から、最初に束ねる行へ。"""
    tree = ast.parse(init.read_text(encoding="utf-8"))
    pairs = [pair for node in tree.body for pair in _bound_by_entry(node, root)]
    return {name: line for name, line in reversed(pairs) if not name.startswith("_")}


#: 新しい process の確かめ: 根を import して入口の公開の名(dir — 遅延の名を含む)のうち module でない物を控え、根の下の
#: package(_ の package を含む)を 1 つずつ import して同じく入口の名を控え、公開の module(移し替えの module の古い path
#: と、移した package の下の古い名を含む)を 1 つずつ import し、そのたびに控えた入口の名が module に替わったかを見る。
#: 最後に入口の名を全部読み(遅延の名を作らせ)、module が返れば同じく数える。import に落ちた module は名を束ねないので
#: 数えず、結末に並べる(赤の文に出す)。
_IMPORTED_PROBE = r"""
import importlib
import importlib.util
import json
import sys
import types

payload = json.loads(sys.argv[1])
sys.path[:0] = payload["first"]
sys.path.extend(place for place in payload["last"] if place not in sys.path)
if importlib.util.find_spec("hy") is not None:
    importlib.import_module("hy")
entries = {root: importlib.import_module(root) for root in payload["roots"]}


def public_names(package):
    module = entries[package]
    return [
        (package, name)
        for name in dir(module)
        if not name.startswith("_") and not isinstance(module.__dict__.get(name), types.ModuleType)
    ]


root_names = [pair for root in payload["roots"] for pair in public_names(root)]


def failure(label, error):
    return [label, f"{type(error).__name__}: {error}"[:200]]


def imported(name):
    try:
        importlib.import_module(name)
    except Exception as error:  # the failure is written into the result, not dropped
        return failure(name, error)
    return None


def bound_modules(names):
    return {
        (root, name): entries[root].__dict__[name].__name__
        for root, name in names
        if isinstance(entries[root].__dict__.get(name), types.ModuleType)
    }


def read(root, name):
    try:
        return getattr(entries[root], name), None
    except Exception as error:  # the failure is written into the result, not dropped
        return None, failure(f"{root}.{name}", error)


# The packages below the roots are imported first (the roots' names are watched meanwhile); their names are
# watched from then on, while the public modules and old paths are imported one by one.
package_steps = [(package, imported(package), bound_modules(root_names)) for package in payload["packages"]]
loaded = [package for package in payload["packages"] if package in sys.modules]
entries = {**entries, **{package: sys.modules[package] for package in loaded}}
names = [*root_names, *(pair for package in loaded for pair in public_names(package))]
steps = [*package_steps, *((name, imported(name), bound_modules(names)) for name in payload["modules"])]
values = {(root, name): read(root, name) for root, name in names}
last = {
    key: value.__name__
    for key, (value, _error) in values.items()
    if isinstance(value, types.ModuleType)
}
first = {
    key: [module, after]
    for after, bound in [("(入口の名を全部読んだ後)", last), *reversed([(n, b) for n, _f, b in steps])]
    for key, module in bound.items()
}
print(json.dumps({
    "found": [[root, name, *first[(root, name)]] for root, name in sorted(first)],
    "failed": [*(f for _n, f, _b in steps if f is not None), *(e for _v, e in values.values() if e is not None)],
}, ensure_ascii=False))
"""


def imported_entry_name_clashes(package_dir: Path) -> tuple[list[str], list[str]]:
    """新しい process の確かめ: 根の下の package と、全部の公開の module と古い path を import した後に module になった
    入口(根と根の下の package の入口)の名と、import に落ちた物。"""
    layout = tool.read_layout(package_dir)
    roots = [root for root in layout.roots if root.path.is_dir()]
    if not roots:
        return [], []
    places = [str(root.path.parent) for root in roots]
    payload = {
        "first": [] if layout.maturin else places,
        "last": places if layout.maturin else [],
        "roots": [root.name for root in roots],
        "packages": [
            name for root in roots for name, _path in entry_packages(root.path, root.name) if name != root.name
        ],
        "modules": [name for name, _path in tool.public_modules(layout) if "." in name],
    }
    done = subprocess.run(
        [sys.executable, "-c", _IMPORTED_PROBE, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=_PROBE_SECONDS,
        check=False,
        cwd=package_dir,
    )
    assert done.returncode == 0, f"{package_dir.name} の入口を新しい process で読めなかった:\n{done.stderr[-2000:]}"
    result = json.loads(done.stdout.strip().splitlines()[-1])
    return (
        [
            f"{root}.{name}: {after} を import した後、入口の名が module {module} に替わった"
            for root, name, module, after in result["found"]
        ],
        [f"{name}: {error}" for name, error in result["failed"]],
    )


def check_entry_names(package_dir: Path) -> None:
    """package の入口の出す名と同じ名の submodule が無い事(字面の照合と、新しい process の確かめの両方 — R34)。"""
    imported, failed = imported_entry_name_clashes(package_dir)
    clashes = [*written_entry_name_clashes(package_dir), *imported]
    assert not clashes, (
        f"{package_dir.name}: 入口の出す名と同じ名の submodule(移し替えの module を含む)を置かない"
        "(ADR-DOE-API-001 R34 — 古い path の import が親の属性を module に束ね、入口の名を隠す・card ki-db44e4d3efe4):\n"
        + "\n".join(f"  {line}" for line in clashes)
        + ("\n  import に落ちた(名を束ねないので数えない): " + "; ".join(failed) if failed else "")
    )


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize("package_dir", [path.name for path in tool.package_dirs(ROOT)])
def test_no_submodule_is_named_like_a_name_its_entry_shows(package_dir: str) -> None:
    """入口(import の根の __init__)の出す名と同じ名の子を根の下に置かず、新しい process で全部の公開の module と古い path を
    import した後も、入口の名が module に替わらない(ADR-DOE-API-001 R34)。"""
    check_entry_names(ROOT / "packages" / package_dir)


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


def _fixture_allowed(helpers: Iterable[str], decision: str = "ki-5b3b2320de40") -> AllowedTable:
    """使い捨ての package の入口の名を、組「許した物 — program の helper」で許した物の表。"""
    return _fixture_grouped({(FIXTURE_ROOT, name): HELPER_GROUP for name in helpers}, decision)


def _fixture_grouped(groups: Mapping[Key, str], decision: str = "ki-d80c9c978b35") -> AllowedTable:
    """使い捨ての package の (module, 名) を、それぞれの組で許した物の表。"""
    return {
        "許した物": [
            {
                "package": FIXTURE_DIR,
                "module": module,
                "name": name,
                "組": groups[(module, name)],
                "決め": decision,
            }
            for module, name in sorted(groups)
        ]
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
    with pytest.raises(AssertionError, match=r"許した物の表に無い[\s\S]*doeff_fixture_api\.helper"):
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


#: Rust の拡張を持つ package(maturin の mixed の形 — 入口は python/ の下・拡張は内側の _native)。組まないので環境に無い。
NATIVE_PYPROJECT = f"""
[project]
name = "{FIXTURE_DIR}"
version = "0"

[build-system]
requires = ["maturin>=1.13,<2.0"]
build-backend = "maturin"

[tool.maturin]
python-source = "python"
module-name = "{FIXTURE_ROOT}._native"
"""


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_native_package_in_scope_that_the_inventory_cannot_import_turns_red(
    tmp_path: Path,
) -> None:
    """Rust の拡張を持つ package を目録が import で読めない(環境に無い)と、字面の読みは入口が拡張から出す名を数えない —
    決まりの範囲なら赤(card ki-1e51e218527e — 前の doeff-indexer の Indexer・SymbolInfo を目録が数えなかった)。名を決めて
    いない範囲の答え待ちの package は、同じ読みで緑。"""
    package = tmp_path / "packages" / FIXTURE_DIR
    (package / "python" / FIXTURE_ROOT).mkdir(parents=True)
    (package / "pyproject.toml").write_text(textwrap.dedent(NATIVE_PYPROJECT), encoding="utf-8")
    (package / "python" / FIXTURE_ROOT / "__init__.py").write_text(
        f"from {FIXTURE_ROOT}._native import Indexer, SymbolInfo\n", encoding="utf-8"
    )
    found = _read_fixture(tmp_path)
    assert not found["exports"], "字面の読みは拡張から出す名を数えない(この検の前提)"
    with pytest.raises(
        AssertionError,
        match=rf"{FIXTURE_DIR}: Rust の拡張を持つ package の module を目録が import で読めず",
    ):
        check_package(found, _fixture_table({}), repo=tmp_path)

    waiting = _fixture_table({})
    waiting["packages"][FIXTURE_DIR]["範囲"] = "範囲の答え待ち"
    check_package(found, waiting, repo=tmp_path)


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
        match=r"許した物の表に無い[\s\S]*"
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
    """許した物の表の組「許した物 — program の helper」に置けるのは、目録の形が program の helper の名だけ(ADR-DOE-API-001 R18)。

    effect を出すだけの program の関数は、許した物の表に置けば緑・どちらの表にも無ければ外れのまま赤。handler を被せる関数
    (handler の値を当てる・WithHandler・中の同じ package の関数が当てる)・effect に答える関数・program を返さない関数を許した物の
    表に置くと赤(名と形を名指す)。許した物の表に在るのに目録に無い名も赤(消した名を表に残した)。
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + PROGRAM_FUNCTIONS + OUTLIER)
    package = _read_fixture(repo)
    forms = {row["name"]: row["form"] for row in package["exports"]}
    assert forms["pinging"] == tool.PROGRAM_HELPER
    rest = ["answering", "helper", "pinging_handled", "pinging_with_handler", "pinging_wrapped"]

    def tables(helpers: list[str]) -> tuple[KnownTable, AllowedTable]:
        listed = sorted(set(rest) - set(helpers))
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), _fixture_allowed(helpers)

    check_package(package, *tables(["pinging"]))
    with pytest.raises(
        AssertionError, match=r"許した物の表に無い[\s\S]*doeff_fixture_api\.pinging"
    ):
        check_package(package, *tables([]))

    past_a_helper = [
        ("pinging_handled", tool.HELPER_INSTALLS, "pinging_handled が ping_handler を読む"),
        (
            "pinging_with_handler",
            tool.HELPER_INSTALLS,
            "pinging_with_handler が WithHandlerType を読む",
        ),
        ("pinging_wrapped", tool.HELPER_INSTALLS, "_install が ping_handler を読む"),
        ("answering", tool.HELPER_ANSWERS, "answering が Resume を読む"),
        ("helper", tool.CONVERSION, "戻りの型 int"),
    ]
    for name, form, said in past_a_helper:
        with pytest.raises(AssertionError, match=re.escape(HELPER_GROUP)) as failure:
            check_package(package, *tables(["pinging", name]))
        assert f"{FIXTURE_ROOT}.{name}({form}・" in str(failure.value)
        assert said in str(failure.value)

    with pytest.raises(
        AssertionError,
        match=r"許した物の表に在るのに目録に無い名が 1 個[\s\S]*doeff_fixture_api\.gone",
    ):
        check_package(package, *tables(["gone", "pinging"]))


CONVERSIONS = '''

import time
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)


def _aware(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    return at


def ms_of(at: datetime) -> int:
    """A conversion: a value in, a value out (the function of its package it reads is one too)."""
    return (_aware(at) - _EPOCH) // _ONE_MS


def _stamp() -> float:
    return time.time()


def ms_now() -> int:
    """Past a conversion: the function of its package it calls reads the clock."""
    return int(_stamp() * 1000)


def ms_today() -> int:
    """Past a conversion: reads the clock by an attribute."""
    return (datetime.now(timezone.utc) - _EPOCH) // _ONE_MS


def pinged(n: int) -> int:
    """Past a conversion: makes an effect."""
    PingEffect()
    return n
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_conversion_can_be_put_into_the_conversion_group(tmp_path: Path) -> None:
    """許した物の表の組「許した物 — 換算の関数」に置けるのは、目録の形が換算の関数の名だけ(ADR-DOE-API-001 R35)。

    値を受けて値を返すだけの関数(中で読む同じ package の関数も同じ)は、許した物の表に置けば緑・どちらの表にも無ければ
    外れのまま赤。中の同じ package の関数が時計を読む・属性で時計を読む・effect を作る関数を置くと赤(組と名と形と読む名を名指す)。
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + CONVERSIONS)
    package = _read_fixture(repo)
    forms = {row["name"]: row["form"] for row in package["exports"]}
    assert forms["ms_of"] == tool.CONVERSION
    rest = ["ms_now", "ms_today", "pinged"]

    def tables(conversions: list[str]) -> tuple[KnownTable, AllowedTable]:
        listed = sorted(set(rest) - set(conversions))
        allowed = _fixture_grouped(
            {(FIXTURE_ROOT, name): CONVERSION_GROUP for name in conversions},
            "lt-YB2AMVK3D876GN4RM1SB3T24AR",
        )
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), allowed

    check_package(package, *tables(["ms_of"]))
    with pytest.raises(AssertionError, match=r"許した物の表に無い[\s\S]*doeff_fixture_api\.ms_of"):
        check_package(package, *tables([]))

    past_a_conversion = [
        ("ms_now", "_stamp が time を読む"),
        ("ms_today", "ms_today が now を読む"),
        ("pinged", "pinged が PingEffect を読む"),
    ]
    for name, said in past_a_conversion:
        with pytest.raises(AssertionError, match=re.escape(CONVERSION_GROUP)) as failure:
            check_package(package, *tables(["ms_of", name]))
        assert f"{FIXTURE_ROOT}.{name}({tool.PLAIN_FUNCTION}・" in str(failure.value)
        assert said in str(failure.value)


#: macro だけを出す module と、macro の外の名も出す module(Hy)と、console script を 1 つ持つ package の宣言。
MACRO_MODULE = """
(defmacro ping! []
  "A macro module's macro."
  '(PingEffect))
"""
MIXED_MODULE = """
(defmacro pong! []
  "A macro beside a function."
  1)

(defn plain [x]
  "A plain function beside the macro."
  x)
"""
COMMAND_PYPROJECT = (
    FIXTURE_PYPROJECT + f'\n[project.scripts]\nfixture-command = "{FIXTURE_ROOT}:helper"\n'
)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_macro_or_a_command_can_be_put_into_the_macro_and_command_groups(
    tmp_path: Path,
) -> None:
    """組「許した物 — Hy の macro」に置けるのは Hy の macro の名と macro だけを出す公開の submodule の行、組「許した物 — CLI
    コマンド」に置けるのは console script の行だけ(ADR-DOE-API-001 R7・R14・R15・R31)。

    macro の module(名に _ の無い submodule)と macro と console script を許した物の表に置けば緑。macro の外の名も出す
    submodule・macro でない関数・console script を macro の組に、macro と関数を CLI コマンドの組に置くと赤(組と名と形を名指す)。
    """
    repo = _fixture_tree(
        tmp_path,
        {
            "__init__.py": EFFECTS_AND_HANDLERS + OUTLIER,
            "macros.hy": MACRO_MODULE,
            "mixed.hy": MIXED_MODULE,
        },
    )
    (repo / "packages" / FIXTURE_DIR / "pyproject.toml").write_text(
        textwrap.dedent(COMMAND_PYPROJECT), encoding="utf-8"
    )
    package = _read_fixture(repo)
    macros, mixed = f"{FIXTURE_ROOT}.macros", f"{FIXTURE_ROOT}.mixed"
    forms = {(row["module"], row["name"]): row["form"] for row in package["exports"]}
    assert forms[(macros, "ping!")] == forms[(mixed, "pong!")] == tool.MACRO_FORM
    assert forms[("(console script)", "fixture-command")] == tool.CONSOLE_SCRIPT
    placed = {
        (macros, "ping!"): MACRO_GROUP,
        (SUBMODULE, macros): MACRO_GROUP,
        (mixed, "pong!"): MACRO_GROUP,
        ("(console script)", "fixture-command"): COMMAND_GROUP,
    }
    outliers = {FIXTURE_ROOT: ["helper"], mixed: ["plain"], SUBMODULE: [mixed]}

    def tables(moved: Mapping[Key, str]) -> tuple[KnownTable, AllowedTable]:
        listed = {
            module: [name for name in names if (module, name) not in moved]
            for module, names in outliers.items()
        }
        return _fixture_table({m: n for m, n in listed.items() if n}), _fixture_grouped(
            {**placed, **moved}
        )

    check_package(package, *tables({}))
    misplaced = [
        ((SUBMODULE, mixed), MACRO_GROUP, f"{mixed}(公開の submodule)(公開の submodule・"),
        ((FIXTURE_ROOT, "helper"), MACRO_GROUP, f"{FIXTURE_ROOT}.helper({tool.CONVERSION}・"),
        (
            ("(console script)", "fixture-command"),
            MACRO_GROUP,
            f"(console script).fixture-command({tool.CONSOLE_SCRIPT}・",
        ),
        ((macros, "ping!"), COMMAND_GROUP, f"{macros}.ping!({tool.MACRO_FORM}・"),
        ((FIXTURE_ROOT, "helper"), COMMAND_GROUP, f"{FIXTURE_ROOT}.helper({tool.CONVERSION}・"),
    ]
    for key, group, said in misplaced:
        with pytest.raises(AssertionError, match=re.escape(f"組「{group}」")) as failure:
            check_package(package, *tables({key: group}))
        assert said in str(failure.value)
        assert ALLOWED_SHAPES[group] in str(failure.value)


def test_an_allowed_group_without_a_check_is_not_well_formed() -> None:
    """許した物の組は、目録で形を確かめる組(ALLOWED_GROUPS)だけを置ける — 確かめの無い組で名を許すと許した物の表の形の検が
    赤。許した物の組を既知の表に置くのも赤(既知の表は外れと境界だけを持ち、空になれる)。"""
    table = _fixture_table({})
    allowed = _fixture_allowed(["helper"])
    assert allowed_problems(allowed, table) == []
    allowed["許した物"][0]["組"] = "許した物 — 便利な関数"
    assert allowed_problems(allowed, table) == [
        f"許した物 {FIXTURE_DIR} {FIXTURE_ROOT}.helper: 組 '許した物 — 便利な関数' は {ALLOWED_GROUPS} のどれでもない(許す組は目録で形を確かめる)"
    ]

    table["packages"][FIXTURE_DIR]["名"] = {HELPER_GROUP: {FIXTURE_ROOT: ["helper"]}}
    assert shape_problems(table, [FIXTURE_DIR]) == [
        f"{FIXTURE_DIR}: 許した物の組 {HELPER_GROUP!r} は既知の表に置かない — 許した物の表 {ALLOWED.relative_to(ROOT)} に"
        "組と決めつきで置く(既知の表は外れと境界だけを持つ)"
    ]


def test_an_allowed_row_without_a_decision_id_is_not_well_formed() -> None:
    """許した物の表は決めの id つきで足せる: 決めが card・依頼・発言の id の行は緑、決めの欄の無い行・id でない決めの行は赤。

    既知の表と同じ名を置く行・鍵の順に並ばない行・範囲の外の package の行も赤。許した物を「足した名」の欄「組」で足す前の道は
    閉じた(欄「組」の在る「足した名」は既知の表の形の検が赤)。
    """
    table = _fixture_table({})
    where = f"許した物 {FIXTURE_DIR} {FIXTURE_ROOT}.pinging"
    assert (
        allowed_problems(_fixture_allowed(["pinging"], "lt-7EK027YNFXTP27QE60J7Q686CZ"), table)
        == []
    )
    assert allowed_problems(_fixture_allowed(["pinging"], "決めた"), table) == [
        f"{where}: 決め '決めた' が card(ki-)・依頼(lt-)・発言(ci-)の id でない"
    ]
    lacking = cast(
        AllowedTable,
        {
            "許した物": [
                {
                    "package": FIXTURE_DIR,
                    "module": FIXTURE_ROOT,
                    "name": "pinging",
                    "組": HELPER_GROUP,
                }
            ]
        },
    )
    assert allowed_problems(lacking, table) == [
        f"{where}: 欄は package・module・name・組・決め(決めの id の無い行は置けない)"
    ]

    assert allowed_problems(
        _fixture_allowed(["pinging"]), _fixture_table({FIXTURE_ROOT: ["pinging"]})
    ) == [f"{where}: 既知の表にも在る(1 つの名は 1 つの表だけに置く)"]
    unordered = _fixture_allowed(["pinging", "sequence"])
    unordered["許した物"].reverse()
    assert allowed_problems(unordered, table) == [
        "許した物の表の行は (package, module, name) の順に並べ、重ねない"
    ]
    outside = _fixture_table({})
    outside["packages"][FIXTURE_DIR] = {"範囲": "範囲の外", "名": {}, "決め": "ki-aaaa56621c5a"}
    assert allowed_problems(_fixture_allowed(["pinging"]), outside) == [
        f"{where}: 範囲の外の package の名は許さない(名を突き合わせない)"
    ]

    old_path = cast(
        AddedName,
        {
            "package": FIXTURE_DIR,
            "module": FIXTURE_ROOT,
            "name": "pinging",
            "訳": "表を足した後に見せる helper(例)",
            "決め": "ki-f20b18c2aad9",
            "組": HELPER_GROUP,
        },
    )
    assert shape_problems(_fixture_table({}, [old_path]), [FIXTURE_DIR]) == [
        f"足した名 {FIXTURE_DIR} {FIXTURE_ROOT}.pinging: 欄は package・module・name・訳・決め"
        f"(許した物は許した物の表 {ALLOWED.relative_to(ROOT)} に置く)"
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
        AssertionError,
        match=r"許した物の表に無い[\s\S]*doeff_fixture_api\.extra\(公開の submodule\)",
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
    with pytest.raises(
        AssertionError, match=r"許した物の表に無い[\s\S]*doeff_fixture_api\.PingResult"
    ):
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


# --- doeff の中の部品は doeff だけが import する(ADR-DOE-API-001 R10・R11 — card ki-9c326052f21c) ---

#: doeff の外の file を探す所(repo の根から): packages/ の下(部品そのものの dir を除く)・examples・docs/adr の Hy。doeff の根の
#: package(doeff/)と doeff の検(tests/)は doeff の中。
OUTSIDE_DOEFF = ("packages", "examples", "docs/adr")
#: 読む file の種類と、探さない dir(組み立ての物と依存 — 名が . で始まる dir も探さない)。
_SOURCE_SUFFIXES = (".py", ".pyi", ".hy")
_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "target"})
#: module の名の文字列を受けて読む関数(importlib.import_module・pytest.importorskip・__import__・importlib.util.find_spec)。
_IMPORTING_CALLS = frozenset({"import_module", "importorskip", "__import__", "find_spec"})
#: Hy の字句: 文字列・註・括弧・それ以外の語(文字列と註の中の括弧を形と読まない)。
_HY_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|;[^\n]*|[()\[\]{}]|[^\s()\[\]{}";]+')
_OPENING = frozenset({"(", "[", "{"})
_CLOSING = frozenset({")", "]", "}"})


@dataclass(frozen=True)
class PartImport:
    """doeff の外の file が doeff の中の部品を import する 1 か所(repo の根からの path・行・import する module)。"""

    path: str
    line: int
    module: str


def _is_part(module: str, roots: frozenset[str]) -> bool:
    """module の名(Hy の - の綴りも)の根が、部品の import の根のどれかか。"""
    return module.replace("-", "_").split(".")[0] in roots


def _source_files(base: Path, skipped: frozenset[Path]) -> Iterator[Path]:
    """base の下の Python と Hy の file(skipped の dir・. で始まる dir・組み立ての物の dir を除く)。"""
    for directory, dirs, files in os.walk(base):
        here = Path(directory)
        dirs[:] = sorted(
            name
            for name in dirs
            if not name.startswith(".") and name not in _SKIPPED_DIRS and here / name not in skipped
        )
        yield from (here / name for name in sorted(files) if name.endswith(_SOURCE_SUFFIXES))


def _python_imports(text: str) -> Iterator[tuple[int, str]]:
    """Python の file が import する module(行と名): import・from(相対でない)・名の文字列を受けて読む関数の呼び。

    構文の誤りで読めない file は、行の頭の import と from の文を字面で読む。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        for number, line in enumerate(text.splitlines(), start=1):
            found = re.match(r"\s*(?:from|import)\s+([\w.]+)", line)
            if found:
                yield number, found.group(1)
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
        elif isinstance(node, ast.Call) and node.args:
            called = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            first = node.args[0]
            if (
                called in _IMPORTING_CALLS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                yield node.lineno, first.value


def _form_modules(tokens: list[tuple[int, str]], start: int) -> Iterator[tuple[int, str]]:
    """Hy の import か require の形の module の語(位置と名): 形のすぐ下の段の語のうち、:as とその次の語と括弧の中を除いた物。

    形は module ごとに「module [名 …]」か「module :as 別名」を並べるので、名の列は括弧の中、別名は :as の次に在る。
    """
    depth, alias_next = 0, False
    for position, token in tokens[start:]:
        if token in _OPENING:
            depth += 1
        elif token in _CLOSING:
            if depth == 0:
                return
            depth -= 1
        elif depth == 0 and alias_next:
            alias_next = False
        elif depth == 0 and token == ":as":
            alias_next = True
        elif depth == 0 and not token.startswith(('"', ":")):
            yield position, token


def _hy_imports(text: str) -> Iterator[tuple[int, str]]:
    """Hy の file が import する module(行と名): (import …)・(require …) の module の語と、名の文字列を受けて読む関数の呼び。"""
    tokens = [(found.start(), found.group()) for found in _HY_TOKEN.finditer(text)]
    tokens = [(position, token) for position, token in tokens if not token.startswith(";")]
    for index, (_position, token) in enumerate(tokens[:-2]):
        if token != "(":
            continue
        head = tokens[index + 1][1]
        if head in ("import", "require"):
            found = _form_modules(tokens, index + 2)
        elif head.replace("-", "_").rsplit(".", 1)[-1] in _IMPORTING_CALLS:
            position, argument = tokens[index + 2]
            found = iter([(position, argument[1:-1])] if argument.startswith('"') else [])
        else:
            continue
        yield from ((text.count("\n", 0, position) + 1, module) for position, module in found)


def part_imports(repo: Path) -> list[PartImport]:
    """doeff の外の file(OUTSIDE_DOEFF の下 — 部品そのものの dir を除く)が doeff の中の部品の import の根を import する所の全部。"""
    roots = frozenset(part.import_root for part in tool.DOEFF_PARTS)
    own = frozenset(repo / "packages" / part.dir for part in tool.DOEFF_PARTS)
    found: list[PartImport] = []
    for base in OUTSIDE_DOEFF:
        for path in _source_files(repo / base, own):
            text = path.read_text(encoding="utf-8", errors="replace")
            if not any(root in text.replace("-", "_") for root in roots):
                continue
            imports = _hy_imports(text) if path.suffix == ".hy" else _python_imports(text)
            relative = path.relative_to(repo).as_posix()
            found.extend(
                PartImport(relative, line, module)
                for line, module in imports
                if _is_part(module, roots)
            )
    return sorted(found, key=lambda row: (row.path, row.line, row.module))


def _part_imports_text(found: list[PartImport]) -> str:
    return (
        "doeff の外の file が doeff の中の部品を import している — 部品は目録が数えない doeff の中の物(ADR-DOE-API-001 R11)。"
        "VM の型は doeff の根から(from doeff import EffectBase, K, Pass, Resume, UnhandledEffect)、doeff の subpackage が要る "
        "VM の内側の物は doeff._vm から読む:\n"
        + "\n".join(f"  {row.path}:{row.line} {row.module}" for row in found)
    )


def test_only_doeff_imports_its_parts() -> None:
    """doeff の外の file(packages/ の下の部品の外の package・examples・docs/adr)は、doeff の中の部品(doeff_vm)を import しない。"""
    found = part_imports(ROOT)
    assert not found, _part_imports_text(found)


#: doeff の外で doeff_vm を読む形(行の末の印 fires の行が赤)と、赤にならない形(doeff の根・doeff._vm・似た名・註と文字列)。
PART_IMPORTS_PY = """
import importlib

import pytest

import doeff_vm  # fires
import doeff_vm.doeff_vm as ext  # fires
from doeff_vm import K  # fires
from doeff_vm._effect_types import declare_passes  # fires
importlib.import_module("doeff_vm.doeff_vm")  # fires
pytest.importorskip("doeff_vm")  # fires

import doeff_vm_like_name
from doeff import K as RootK
from doeff._vm import native

# from doeff_vm import K
SAID = "doeff_vm"
"""
PART_IMPORTS_HY = """
(import doeff-vm [K])  ; fires
(import doeff_vm.doeff_vm :as ext)  ; fires
(import pytest doeff_vm [Pass])  ; fires
(require doeff-vm [m])  ; fires
(pytest.importorskip "doeff_vm")  ; fires

(import doeff [K Pass])
(import doeff._vm [native] doeff-vm-like-name :as like)
;; (import doeff_vm [K])
(setv SAID "(import doeff_vm)")
"""
#: 同じ所を doeff の根と doeff._vm から読む形(緑)。
ROOT_IMPORTS_PY = """
from doeff import K, Pass
from doeff._vm import declare_passes, native
"""
ROOT_IMPORTS_HY = """
(import doeff [K Pass])
(import doeff._vm [native])
"""
#: doeff の外の file(赤になる所)と、doeff の中の file(部品そのもの・doeff の根の package・doeff の検 — 数えない所)。
OUTSIDE_FILES = {
    f"packages/{FIXTURE_DIR}/src/{FIXTURE_ROOT}/__init__.py": PART_IMPORTS_PY,
    f"packages/{FIXTURE_DIR}/src/{FIXTURE_ROOT}/hy_side.hy": PART_IMPORTS_HY,
    "examples/vm_sample.py": PART_IMPORTS_PY,
    "docs/adr/defadr_vm_sample.hy": PART_IMPORTS_HY,
}
INSIDE_FILES = {
    "packages/doeff-vm/doeff_vm/__init__.py": PART_IMPORTS_PY,
    "doeff/__init__.py": PART_IMPORTS_PY,
    "tests/test_vm_sample.py": PART_IMPORTS_PY,
}


def _write_files(repo: Path, files: Mapping[str, str]) -> None:
    for relative, source in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


def test_a_doeff_part_imported_outside_doeff_turns_red(tmp_path: Path) -> None:
    """doeff の外の file が doeff の中の部品(doeff_vm)を import すると赤 — 素の import・from・下の module・Hy の import と require
    (1 つの形に module を並べる形も)・importlib.import_module と pytest.importorskip に渡す名の文字列の行を名指す。doeff の根と
    doeff._vm から読む形・似た名・註と文字列は数えず、部品そのもの・doeff の根の package・doeff の検(tests/)の import も数えない。
    外の file を doeff の根から読む形に直すと緑(ADR-DOE-API-001 R11)。"""
    _write_files(tmp_path, {**OUTSIDE_FILES, **INSIDE_FILES})
    found = part_imports(tmp_path)
    marked = {
        (relative, number)
        for relative, source in OUTSIDE_FILES.items()
        for number, line in enumerate(source.splitlines(), start=1)
        if line.rstrip().endswith("fires")
    }
    assert {(row.path, row.line) for row in found} == marked
    assert len(found) == len(marked) == 22
    assert {row.module.replace("-", "_") for row in found} == {
        "doeff_vm",
        "doeff_vm.doeff_vm",
        "doeff_vm._effect_types",
    }
    assert "  examples/vm_sample.py:6 doeff_vm" in _part_imports_text(found).splitlines()

    _write_files(
        tmp_path,
        {
            relative: ROOT_IMPORTS_HY if relative.endswith(".hy") else ROOT_IMPORTS_PY
            for relative in OUTSIDE_FILES
        },
    )
    assert part_imports(tmp_path) == []


#: 隠した時の名の固定の列を印の下に持つ移し替えの module(tools/hide_submodules.py の書く形 — card ki-4fbed45b5863)。
PINNED_SHIM = MARKED_SHIM.replace(
    "\n",
    f"\n# moved-submodule-shim-names {FIXTURE_ROOT}.types:\n#   LIMIT PingResult\n#   helper\n",
    1,
)
#: 隠した後に、移した先の _ の module へ書き手が足した内側の helper。
ADDED_INSIDE = '''

def added_helper(x: int) -> int:
    """A helper added inside the hidden module after it was hidden."""
    return x * 2
'''
#: 入口に書き手が足した名(意図して見せる名)。
ADDED_AT_ROOT = '''

def root_helper(x: int) -> int:
    """A helper the writer adds to the root on purpose."""
    return x - 2
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_name_added_inside_a_pinned_shim_does_not_grow_the_public_api(tmp_path: Path) -> None:
    """移し替えの module が名の固定の列を持てば、移した先の _ の module に helper を足しても目録の行は増えず緑。

    列の無い移し替えの module では、古い path が移した先の module そのものを返すので、足した helper が古い path の
    公開名に加わって赤になる(card ki-4fbed45b5863 — 内側の helper を足すだけで公開 API が増えた)。入口に足した名は
    書き手が意図して見せる名なので、列が在っても赤(表に足すか直す)。
    """
    child = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [child], child: ["LIMIT", "PingResult", "helper"]})
    pinned = _fixture_tree(
        tmp_path / "pinned",
        {
            "__init__.py": EFFECTS_AND_HANDLERS,
            "_types.py": TYPES + ADDED_INSIDE,
            "types.py": PINNED_SHIM,
        },
    )
    package = _read_fixture(pinned)
    check_package(package, table)
    assert {row["name"] for row in package["exports"] if row["module"] == child} == {
        "LIMIT",
        "PingResult",
        "helper",
    }

    unpinned = _fixture_tree(
        tmp_path / "unpinned",
        {
            "__init__.py": EFFECTS_AND_HANDLERS,
            "_types.py": TYPES + ADDED_INSIDE,
            "types.py": MARKED_SHIM,
        },
    )
    with pytest.raises(AssertionError, match=rf"許した物の表に無い[\s\S]*{child}\.added_helper"):
        check_package(_read_fixture(unpinned), table)

    at_root = _fixture_tree(
        tmp_path / "at-root",
        {
            "__init__.py": EFFECTS_AND_HANDLERS + ADDED_AT_ROOT,
            "_types.py": TYPES + ADDED_INSIDE,
            "types.py": PINNED_SHIM,
        },
    )
    with pytest.raises(
        AssertionError, match=rf"許した物の表に無い[\s\S]*{FIXTURE_ROOT}\.root_helper"
    ) as failure:
        check_package(_read_fixture(at_root), table)
    assert "added_helper" not in str(failure.value)


#: namespace の dir(__init__ の無い _types)を指す移し替えの module に、下の module の名の固定の列を書いた形。
PINNED_NAMESPACE_SHIM = MARKED_SHIM.replace(
    "\n", f"\n# moved-submodule-shim-names {FIXTURE_ROOT}.types.sub:\n#   sub_helper\n", 1
)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_name_added_below_a_pinned_namespace_dir_shim_does_not_grow_the_public_api(
    tmp_path: Path,
) -> None:
    """namespace の dir を指す移し替えの module の名の固定の列も読み、移した先の _ の下に helper を足しても緑。

    その移し替えの module は module でないので、目録が module の列だけから列を読むと読まれず、足した helper が古い path の
    公開名に加わって赤に戻った(doeff-cluster の区画 _sim・_record_store — card ki-fcf6342de575)。列の無い時は、
    下の古い path を列の無い path に数える(tools/hide_submodules.py pin が列を書く移し替えの module に拾う)。
    """
    below = f"{FIXTURE_ROOT}.types.sub"
    table = _fixture_table({SUBMODULE: [below], below: ["sub_helper"]})
    files = {"__init__.py": EFFECTS_AND_HANDLERS, "_types/sub.py": BELOW + ADDED_INSIDE}
    pinned = _fixture_tree(tmp_path / "pinned", {**files, "types.py": PINNED_NAMESPACE_SHIM})
    package = _read_fixture(pinned)
    check_package(package, table)
    assert {row["name"] for row in package["exports"] if row["module"] == below} == {"sub_helper"}
    layout = tool.read_layout(pinned / "packages" / FIXTURE_DIR)
    shim = pinned / "packages" / FIXTURE_DIR / "src" / FIXTURE_ROOT / "types.py"
    assert tool.shim_modules(layout) == [(f"{FIXTURE_ROOT}.types", shim)]
    assert tool.unpinned_shim_modules(layout) == []

    unpinned = _fixture_tree(tmp_path / "unpinned", {**files, "types.py": MARKED_SHIM})
    with pytest.raises(AssertionError, match=rf"許した物の表に無い[\s\S]*{below}\.added_helper"):
        check_package(_read_fixture(unpinned), table)
    assert tool.unpinned_shim_modules(tool.read_layout(unpinned / "packages" / FIXTURE_DIR)) == [
        below
    ]


@pytest.mark.parametrize("package_dir", [path.name for path in tool.package_dirs(ROOT)])
def test_every_moved_submodule_shim_pins_the_names_of_its_old_paths(package_dir: str) -> None:
    """移し替えの module の全部が、自分と下の module の古い path ごとに名の固定の列を持つ(列の無い古い path は、
    移した先の _ の module に足した名を全部公開名に数えてしまう — tools/hide_submodules.py pin で列を書く)。"""
    layout = tool.read_layout(ROOT / "packages" / package_dir)
    assert not tool.unpinned_shim_modules(layout), (
        f"{package_dir}: 名の固定の列の無い移し替えの古い path が在る — "
        f"`uv run python tools/hide_submodules.py pin --package packages/{package_dir}` で書く: "
        + ", ".join(tool.unpinned_shim_modules(layout))
    )


#: 入口が遅延で関数 serve を出し(doeff_core_effects の aiohttp_http_server の形)、すぐに関数 tool を出す(doeff_agentic の
#: tmux_handler の形)package。どちらの関数も _ の module に在り、古い path の子 serve・tool は移し替えの module。
CLASHING_ENTRY = f"""
import importlib as _importlib

from {FIXTURE_ROOT}._tool import tool as tool

_LAZY_EXPORTS = {{"serve": "{FIXTURE_ROOT}._serve"}}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(_importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({{*globals(), *_LAZY_EXPORTS}})
"""


def _moved_shim(child: str) -> str:
    """古い path の子 ``child`` の移し替えの module(tools/hide_submodules.py の殻と同じく sys.modules を差し替える)。"""
    return (
        f"# moved-submodule-shim: {FIXTURE_ROOT}.{child} -> {FIXTURE_ROOT}._{child}\n"
        f"# moved-submodule-shim-names {FIXTURE_ROOT}.{child}:\n"
        f"#   {child}\n"
        "import sys\n"
        "from importlib import import_module\n\n"
        f'sys.modules[__name__] = import_module("{FIXTURE_ROOT}._{child}")\n'
    )


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_submodule_named_like_an_entry_name_turns_red_and_removing_it_turns_green(
    tmp_path: Path,
) -> None:
    """入口が出す関数 serve(遅延)・tool(すぐ)と同じ名の移し替えの module を置くと、字面の照合も新しい process の確かめも
    赤(どの古い path の import で名が module に替わったかを名指す)・2 つの殻を消すと緑(ADR-DOE-API-001 R34)。

    doeff 42f897000 の doeff_core_effects の殻 aiohttp_http_server.py と同じ形で、10-30 に記録の service が起動の度に
    ``with_handlers: handler must be callable, got module`` で落ちた(card ki-db44e4d3efe4)。
    """
    files = {
        "__init__.py": CLASHING_ENTRY,
        "_serve.py": 'def serve() -> str:\n    return "serve"\n',
        "_tool.py": 'def tool() -> str:\n    return "tool"\n',
        "serve.py": _moved_shim("serve"),
        "tool.py": _moved_shim("tool"),
    }
    package_dir = _fixture_tree(tmp_path, files) / "packages" / FIXTURE_DIR
    written = written_entry_name_clashes(package_dir)
    imported, failed = imported_entry_name_clashes(package_dir)
    assert [line.split(":")[0] for line in written] == [f"{FIXTURE_ROOT}.serve", f"{FIXTURE_ROOT}.tool"]
    assert imported == [
        f"{FIXTURE_ROOT}.serve: {FIXTURE_ROOT}.serve を import した後、入口の名が module {FIXTURE_ROOT}._serve に替わった",
        f"{FIXTURE_ROOT}.tool: {FIXTURE_ROOT}.tool を import した後、入口の名が module {FIXTURE_ROOT}._tool に替わった",
    ]
    assert failed == []
    with pytest.raises(AssertionError, match=r"ADR-DOE-API-001 R34"):
        check_entry_names(package_dir)

    for child in ("serve", "tool"):
        (package_dir / "src" / FIXTURE_ROOT / f"{child}.py").unlink()
    check_entry_names(package_dir)


def _moved_package_shim(child: str, names: Mapping[str, str]) -> str:
    """古い path の子 package ``child`` の移し替えの module: 古い path も、その下の古い名も(meta path の finder が)移した先の
    module そのもので答える(tools/hide_submodules.py の package の殻と同じ形)。``names`` は古い module の名から固定の列へ。"""
    old, new = f"{FIXTURE_ROOT}.{child}", f"{FIXTURE_ROOT}._{child}"
    pins = "".join(f"# moved-submodule-shim-names {module}:\n#   {pinned}\n" for module, pinned in names.items())
    return (
        f"# moved-submodule-shim: {old} -> {new}\n{pins}"
        "import importlib.abc\n"
        "import importlib.util\n"
        "import sys\n"
        "from importlib import import_module\n\n\n"
        "class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        f'        moved = "{new}" + fullname[len("{old}"):]\n'
        f'        if not fullname.startswith("{old}.") or importlib.util.find_spec(moved) is None:\n'
        "            return None\n"
        "        return importlib.util.spec_from_loader(fullname, self)\n\n"
        "    def create_module(self, spec):\n"
        "        return None\n\n"
        "    def exec_module(self, module):\n"
        f'        sys.modules[module.__name__] = import_module("{new}" + module.__name__[len("{old}"):])\n\n\n'
        "sys.meta_path.insert(0, _MovedSubmodules())\n"
        f'sys.modules[__name__] = import_module("{new}")\n'
    )


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_inner_package_entry_name_like_its_child_turns_red_and_moving_the_child_turns_green(
    tmp_path: Path,
) -> None:
    """根の下の package _inner の入口が、同じ名の子 tool から関数 tool を束ね、移した package の古い path inner を殻が答えると、
    字面の照合も新しい process の確かめも赤(古い path inner.tool の import が _inner の属性 tool を module で上書きする)・
    子を _ の名 _tool へ移すと緑(ADR-DOE-API-001 R34)。

    doeff_agents._effects の子 agent と、移した package の古い path doeff_agents.effects.agent と同じ形(card ki-d3a096ce3eaa)。
    """
    files = {
        "__init__.py": "",
        "_inner/__init__.py": f"from {FIXTURE_ROOT}._inner.tool import tool as tool\n",
        "_inner/tool.py": 'def tool() -> str:\n    return "tool"\n',
        "inner.py": _moved_package_shim(
            "inner", {f"{FIXTURE_ROOT}.inner": "tool", f"{FIXTURE_ROOT}.inner.tool": "tool"}
        ),
    }
    package_dir = _fixture_tree(tmp_path, files) / "packages" / FIXTURE_DIR
    written = written_entry_name_clashes(package_dir)
    imported, failed = imported_entry_name_clashes(package_dir)
    assert [line.split(":")[0] for line in written] == [f"{FIXTURE_ROOT}._inner.tool"]
    assert imported == [
        f"{FIXTURE_ROOT}._inner.tool: {FIXTURE_ROOT}.inner.tool を import した後、入口の名が module"
        f" {FIXTURE_ROOT}._inner.tool に替わった",
    ]
    assert failed == []
    with pytest.raises(AssertionError, match=r"ADR-DOE-API-001 R34"):
        check_entry_names(package_dir)

    inner = package_dir / "src" / FIXTURE_ROOT / "_inner"
    (inner / "tool.py").rename(inner / "_tool.py")
    (inner / "__init__.py").write_text(f"from {FIXTURE_ROOT}._inner._tool import tool as tool\n", encoding="utf-8")
    check_entry_names(package_dir)
