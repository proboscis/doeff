"""submodule を _ へ移す書き換えの道具(tools/hide_submodules.py)が、呼び手を壊さずに 3 つの stage を進める事。

使い捨ての repo に、子 2 つ(Python の module effects と、Hy の module と python -m で走る module を持つ package tools)の
package と、Python と Hy の呼び手(mock.patch の文字列・python -m・Hy の require を含む)を置く。外の repo にも同じ呼び手を置く。

1. hide の前・hide の後(doeff の木の呼び手は書き換えた形・外の repo は古い path のまま移し替えの module を通る)・
   外の repo に rewrite を当てた後・finish の後のどれでも、両方の呼び手の test が緑。
2. 名の行き先: effect と handler と既知の表の境界の名は入口へ、外れの名は _ に残り、呼び手は入口か _ の path から読む。
3. 歴史の記録(docs/design)は書き換えず、markdown の code の囲みは書き換える。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    """tools/ の道具を読む(tools/ は package ではない)。"""
    loaded = sys.modules.get("hide_submodules")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "hide_submodules", ROOT / "tools" / "hide_submodules.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hide_submodules"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()

PACKAGE = "doeff-fixture-hide"
ROOT_NAME = "doeff_fixture_hide"

FILES = {
    f"packages/{PACKAGE}/pyproject.toml": f"""
        [project]
        name = "{PACKAGE}"
        version = "0"

        [project.scripts]
        fixture-cli = "{ROOT_NAME}.tools.cli:_main"

        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [tool.hatch.build.targets.wheel]
        packages = ["src/{ROOT_NAME}"]
    """,
    f"packages/{PACKAGE}/src/{ROOT_NAME}/__init__.py": f'''
        """The fixture package."""

        from {ROOT_NAME}.effects import PingEffect as PingEffect
    ''',
    f"packages/{PACKAGE}/src/{ROOT_NAME}/effects.py": '''
        from dataclasses import dataclass

        from doeff import EffectBase, Pass, handler


        class PingEffect(EffectBase):
            """The one effect."""


        @dataclass(frozen=True)
        class PingConfig:
            """The handler's settings."""

            name: str


        @handler
        def ping_handler(effect: PingEffect, k):
            yield Pass()


        def helper(x: int) -> int:
            return x + 1


        def call_helper(x: int) -> int:
            return helper(x)
    ''',
    f"packages/{PACKAGE}/src/{ROOT_NAME}/tools/__init__.py": f'''
        """Text tools (Hy)."""

        import hy  # noqa: F401 - lets the Hy module below be imported

        from {ROOT_NAME}.tools.text import Shout as Shout
    ''',
    f"packages/{PACKAGE}/src/{ROOT_NAME}/tools/text.hy": """
        (import doeff [EffectBase])

        (defclass Shout [EffectBase]
          "Shout a word.")

        (defn shout [word]
          (+ (.upper word) "!"))

        (defmacro twice [form]
          `(+ ~form ~form))
    """,
    f"packages/{PACKAGE}/src/{ROOT_NAME}/tools/cli.py": """
        def _main() -> None:
            print("fixture cli")


        if __name__ == "__main__":
            _main()
    """,
    "tests/data/public_api_known_violations.json": json.dumps(
        {
            "packages": {
                PACKAGE: {
                    "範囲": "決まりの範囲",
                    "名": {
                        "外れ — 内側の module へ移す": {
                            f"{ROOT_NAME}.effects": ["call_helper", "helper"],
                            f"{ROOT_NAME}.tools.text": ["shout"],
                        },
                        "境界 — handler の設定の型": {f"{ROOT_NAME}.effects": ["PingConfig"]},
                        "境界 — Hy の macro": {f"{ROOT_NAME}.tools.text": ["twice"]},
                        "境界 — CLI": {"(console script)": ["fixture-cli"]},
                        "外れ — submodule を _ で隠す": {
                            "(submodule)": [
                                f"{ROOT_NAME}.effects",
                                f"{ROOT_NAME}.tools",
                                f"{ROOT_NAME}.tools.cli",
                                f"{ROOT_NAME}.tools.text",
                            ]
                        },
                    },
                }
            },
            "足した名": [],
        },
        ensure_ascii=False,
        indent=2,
    ),
    "packages/doeff-linter/data/world_handlers.json": json.dumps(
        {
            "_not_listed": {f"{ROOT_NAME}.effects:call_helper": "値を返すだけ"},
            "handlers": [
                {"handler": f"{ROOT_NAME}.effects:ping-handler", "touches": ["network"], "why": "ping"},
                {"handler": f"{ROOT_NAME}.tools.text:shout", "touches": ["file"], "why": "shout"},
                {"handler": "elsewhere.io:real-handler", "touches": ["file"], "why": "外の package"},
            ],
        },
        ensure_ascii=False,
        indent=1,
    ),
    "docs/guide.md": f"""
        # Guide

        ```python
        from {ROOT_NAME}.effects import PingEffect, helper
        ```

        The module `{ROOT_NAME}.effects` holds the effect; {ROOT_NAME}.effects is named in prose too.
    """,
    "docs/design/record.md": f"""
        # Record

        On 2026-10-28 the effect lived in `{ROOT_NAME}.effects` ({ROOT_NAME}/effects.py).
    """,
}

CALLERS = {
    "pytest.ini": "[pytest]\n",
    "conftest.py": """
        import hy  # noqa: F401 - the tests import the Hy caller
    """,
    "hy_caller.hy": f"""
        (import {ROOT_NAME}.tools.text [Shout shout])
        (require {ROOT_NAME}.tools.text [twice])

        (defn loud [word]
          (twice (shout word)))

        (defn shout-type []
          Shout)
    """,
    "test_callers.py": f"""
        import subprocess
        import sys
        from unittest import mock

        import hy_caller

        import {ROOT_NAME}.tools.text as text_module
        from {ROOT_NAME} import tools
        from {ROOT_NAME}.effects import PingConfig, PingEffect, call_helper, helper


        def test_names_read_through_the_imports():
            assert PingConfig(name="x").name == "x"
            assert isinstance(PingEffect, type)
            assert helper(1) == 2
            assert tools.Shout is text_module.Shout is hy_caller.shout_type()


        def test_a_patch_on_the_module_path_reaches_the_code():
            with mock.patch("{ROOT_NAME}.effects.helper", return_value=7):
                assert call_helper(1) == 7


        def test_the_hy_caller_reads_a_function_and_a_macro():
            assert hy_caller.loud("a") == "A!A!"


        def test_the_module_runs_with_python_m():
            finished = subprocess.run(
                [sys.executable, "-m", "{ROOT_NAME}.tools.cli"], capture_output=True, text=True, check=True
            )
            assert finished.stdout.strip() == "fixture cli"
    """,
}


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.com",
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _write_repo(repo: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _run_callers(directory: Path, src: Path) -> None:
    """呼び手の test を別の process の pytest で走らせ、緑を確かめる。"""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(src), str(directory)]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    finished = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(directory),
            str(directory),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=directory,
        check=False,
    )
    assert finished.returncode == 0, (
        f"{directory} の呼び手の test が赤:\n{finished.stdout}\n{finished.stderr}"
    )


def _text(repo: Path, relative: str) -> str:
    return (repo / relative).read_text(encoding="utf-8")


def _check_table(repo: Path) -> None:
    """使い捨ての package の目録が、その repo の既知の表と合う(表の test と同じ突き合わせ)。"""
    [package] = tool._inventory_tool().inventory(repo, [PACKAGE], 1, 100)
    table = json.loads(_text(repo, "tests/data/public_api_known_violations.json"))
    tool._table_guard().check_package(package, table)


CATALOG = "packages/doeff-linter/data/world_handlers.json"


def _catalog_spellings(repo: Path) -> list[str]:
    return [row["handler"] for row in json.loads(_text(repo, CATALOG))["handlers"]]


def _catalog_missing_old_spellings(repo: Path) -> list[str]:
    """doeff-linter の検 catalog_keeps_the_old_spelling_of_every_moved_handler と同じ条件の抜け。

    1 行目に移し替えの印を持つ module が残る間、移した先の _ の綴り(子が package ならその下の module も)の行には、
    古い綴りの行も目録に要る(linter は :wraps の綴りを目録の鍵と完全一致で照らす)。
    """
    marks = (
        path.read_text(encoding="utf-8").split("\n", 1)[0] for path in (repo / "packages").rglob("*.py")
    )
    moved_from = {
        new.strip(): old.strip()
        for first in marks
        if first.startswith(tool.SHIM_MARK)
        for old, new in [first[len(tool.SHIM_MARK) :].split(" -> ")]
    }
    spellings = _catalog_spellings(repo)
    known = {tool._catalog_target(s) for s in spellings}
    return [
        f"{spelling} の古い綴り {old_spelling}"
        for spelling in spellings
        for module, name in [spelling.split(":", 1)]
        for new, old in moved_from.items()
        if module == new or module.startswith(new + ".")
        for old_spelling in [f"{old}{module[len(new) :]}:{name}"]
        if tool._catalog_target(old_spelling) not in known
    ]


#: 道具の Report(tools/ は package でなく、型を import できない — 道具を spec から読む所の 1 か所の型)。
Report = Any


def _assert_moved_and_entry(repo: Path, report: Report) -> None:
    """stage 1 の後: 子は _ へ移り、元の所は移し替えの module、入口は遅延の名で effect・handler・境界の名を出す。"""
    package_root = repo / "packages" / PACKAGE / "src" / ROOT_NAME
    assert (package_root / "_effects.py").is_file()
    assert (package_root / "_tools" / "text.hy").is_file()
    assert tool.is_shim(package_root / "effects.py", tool.Child(ROOT_NAME, "effects", package_root))
    assert tool.is_shim(package_root / "tools.py", tool.Child(ROOT_NAME, "tools", package_root))
    assert not (package_root / "tools").exists()
    assert sorted(report.entry_added) == ["PingConfig", "Shout", "ping_handler"]
    names = report.names
    assert names["入口に既に在る"] == [f"{ROOT_NAME}.effects.PingEffect"]
    assert sorted(names["_ に残す"]) == [
        f"{ROOT_NAME}.effects.call_helper",
        f"{ROOT_NAME}.effects.helper",
        f"{ROOT_NAME}.tools.text.shout",
    ]
    assert names["Hy の macro(_ に残す)"] == [f"{ROOT_NAME}.tools.text.twice"]
    assert report.conflicts == ()
    entry = _text(repo, f"packages/{PACKAGE}/src/{ROOT_NAME}/__init__.py")
    assert f"from {ROOT_NAME}._effects import PingEffect as PingEffect" in entry
    assert f'"PingConfig": "{ROOT_NAME}._effects"' in entry
    assert f'"Shout": "{ROOT_NAME}._tools.text"' in entry
    assert f"    from {ROOT_NAME}._effects import ping_handler as ping_handler" in entry
    assert "Shout as Shout" not in entry  # Hy の module の名は型の検に見せない(.pyi が無い)
    tree = ast.parse(entry)
    assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == {
        "__getattr__",
        "__dir__",
    }
    assigned = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert "__all__" not in assigned


def _assert_rewritten(repo: Path, report: Report, record_before: str) -> None:
    """stage 1 の後: 呼び手は入口か _ の path から読み、歴史の記録と地の文は書き換えない。"""
    callers = _text(repo, "callers/test_callers.py")
    assert f"from {ROOT_NAME} import PingConfig, PingEffect\n" in callers
    assert f"from {ROOT_NAME}._effects import call_helper, helper\n" in callers
    assert f"from {ROOT_NAME} import _tools as tools\n" in callers
    assert f"import {ROOT_NAME}._tools.text as text_module\n" in callers
    assert f'mock.patch("{ROOT_NAME}._effects.helper"' in callers
    assert f'"-m", "{ROOT_NAME}._tools.cli"' in callers
    hy_caller = _text(repo, "callers/hy_caller.hy")
    assert f"(import {ROOT_NAME} [Shout]\n        {ROOT_NAME}._tools.text [shout])" in hy_caller
    assert f"(require {ROOT_NAME}._tools.text [twice])" in hy_caller
    assert f'fixture-cli = "{ROOT_NAME}._tools.cli:_main"' in _text(
        repo, f"packages/{PACKAGE}/pyproject.toml"
    )
    guide = _text(repo, "docs/guide.md")
    assert f"from {ROOT_NAME} import PingEffect\nfrom {ROOT_NAME}._effects import helper\n" in guide
    assert f"`{ROOT_NAME}._effects`" in guide
    assert f"{ROOT_NAME}.effects is named in prose" in guide  # 地の文は書き換えない
    assert _text(repo, "docs/design/record.md") == record_before
    assert report.remaining == {"docs/guide.md": 1}
    assert report.rewrite.outliers_to_underscore == (
        f"{ROOT_NAME}.effects.call_helper",
        f"{ROOT_NAME}.effects.helper",
        f"{ROOT_NAME}.tools.text.shout",
    )
    assert report.rewrite.rewritten[tool.PY_IMPORT] >= 4
    assert report.rewrite.rewritten[tool.HY_REQUIRE] == 1
    assert report.rewrite.rewritten[tool.STRING] == 2


def _row(module: str, name: str, kind: str, defined_at: str) -> dict[str, str]:
    """目録の行 1 つ(decide_names に渡す形)。"""
    return {
        "module": f"{ROOT_NAME}.{module}",
        "name": name,
        "kind": kind,
        "form": "class",
        "defined_at": f"packages/{PACKAGE}/src/{ROOT_NAME}/{defined_at}",
        "reason": "",
    }


def test_a_name_a_child_imports_from_another_child_goes_to_the_entry_once(tmp_path: Path) -> None:
    """子が別の子の名を出し直す時は定義の在る子から 1 度だけ入口へ、定義が違う同じ名はぶつかりに残す。

    失敗ケース: doeff-codex の effects・fake・handler は values の CodexInput を import して出し直す。子ごとに同じ名を
    入口へ出すと読んで全部をぶつかりにすると、effect の値の型も effect も入口に出ない(hide の 1 回目で 100 個)。
    """
    children = [tool.Child(ROOT_NAME, name, tmp_path) for name in ("effects", "lines", "values")]
    boundary = "境界 — effect の値の型"
    groups = {
        (f"{ROOT_NAME}.{module}", name): boundary
        for module, name in (
            ("effects", "Spec"),
            ("values", "Spec"),
            ("effects", "Started"),
            ("lines", "Started"),
        )
    }
    rows = [
        _row("effects", "Ping", "effect", "effects.py:9"),
        _row("effects", "Spec", "other", "values.py:3"),
        _row("values", "Spec", "other", "values.py:3"),
        _row("effects", "Started", "other", "effects.py:5"),
        _row("lines", "Started", "other", "lines.hy:7"),
    ]
    fates = {(f.modules[0], f.name): f for f in tool.decide_names(rows, children, groups, set())}
    spec = fates[(f"{ROOT_NAME}.values", "Spec")]
    assert (spec.fate, spec.source) == ("entry", f"{ROOT_NAME}._values")
    assert fates[(f"{ROOT_NAME}.effects", "Spec")].fate == "already"
    assert fates[(f"{ROOT_NAME}.effects", "Ping")].fate == "entry"
    assert {fates[(f"{ROOT_NAME}.{m}", "Started")].fate for m in ("effects", "lines")} == {
        "conflict"
    }


def test_a_name_in_the_allowed_table_goes_to_the_entry_and_finish_drops_its_row(
    tmp_path: Path,
) -> None:
    """許した物の表の名(R18 の program の helper)は、既知の表の境界の名と同じく入口へ出す(外れの名は _ に残す)。stage 3
    で目録から消えた許した物の行は、許した物の表から組ごとに数えて消す。

    失敗ケース: 許した物を既知の表から許した物の表へ分けた時、道具が既知の表だけを読むと helper を
    「表に無い名」と読んで _ に残し、外の repo が入口から読めなくなる。
    """
    helper_group = "許した物 — program の helper"
    children = [tool.Child(ROOT_NAME, "effects", tmp_path)]
    table = {
        "packages": {
            PACKAGE: {
                "範囲": "決まりの範囲",
                "名": {"外れ — 内側の module へ移す": {f"{ROOT_NAME}.effects": ["helper"]}},
            }
        },
        "足した名": [],
    }
    allowed: dict[str, object] = {
        "許した物": [
            {
                "package": PACKAGE,
                "module": f"{ROOT_NAME}.effects",
                "name": "pinging",
                "組": helper_group,
                "決め": "ADR-DOE-API-001 R18",
            }
        ]
    }
    groups, added = tool._table_groups(table, allowed, PACKAGE)
    rows = [
        _row("effects", "pinging", "other", "effects.py:3"),
        _row("effects", "helper", "other", "effects.py:9"),
    ]
    fates = {f.name: (f.fate, f.group) for f in tool.decide_names(rows, children, groups, added)}
    assert fates == {
        "pinging": ("entry", helper_group),
        "helper": ("underscore", "外れ — 内側の module へ移す"),
    }

    path = tmp_path / "public_api_allowed.json"
    path.write_text(json.dumps(allowed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    assert (
        tool.drop_allowed_rows(path, allowed, PACKAGE, {(f"{ROOT_NAME}.effects", "helper")}) == {}
    )
    pinging = {(f"{ROOT_NAME}.effects", "pinging")}
    assert tool.drop_allowed_rows(path, allowed, PACKAGE, pinging) == {helper_group: 1}
    assert json.loads(path.read_text(encoding="utf-8"))["許した物"] == []


def test_a_name_a_child_imports_from_outside_the_children_is_read_from_its_definition(
    tmp_path: Path,
) -> None:
    """子が隠さない区画の名を出し直す時、入口はその名を定義の在る module から読む。

    失敗ケース: doeff-cluster の sim を隠すと、sim の local が shared の effect 75 個を import して出し直していて、
    入口が ``_sim.local`` から読んだ — effect を 1 つ読むだけで模擬の cluster の全部を読み込む。
    """
    children = [tool.Child(ROOT_NAME, "sim", tmp_path)]
    rows = [
        _row("sim.local", "AckOutbox", "effect", "shared/intent/model.hy:5"),
        _row("shared.intent.model", "AckOutbox", "effect", "shared/intent/model.hy:5"),
        _row("sim.local", "SimWorker", "effect", "sim/local.hy:9"),
    ]
    fates = {(f.modules[0], f.name): f for f in tool.decide_names(rows, children, {}, set())}
    ack = fates[(f"{ROOT_NAME}.sim.local", "AckOutbox")]
    assert (ack.fate, ack.source) == ("entry", f"{ROOT_NAME}.shared.intent.model")
    worker = fates[(f"{ROOT_NAME}.sim.local", "SimWorker")]
    assert (worker.fate, worker.source) == ("entry", f"{ROOT_NAME}._sim.local")


def test_used_by_shows_the_outliers_an_outside_tree_reads_and_keeps_unread_added_names_hidden(
    tmp_path: Path,
) -> None:
    """``--used-by`` の木が読む名は表の組が「外れ」でも入口へ、読まない「足した名」は ``_`` に残す。

    失敗ケース: doeff-cluster は外の repo が使う名だけを入口から見せる(ADR-DOE-API-001 R15)。表の組だけで分けると、
    使い手の書く ``(import doeff_cluster [sim-cluster])`` の sim-cluster が「外れ — 内側の module へ移す」で入口に出ず、
    表の「足した名」の MODULE_TAGS が入口に出た。
    """
    children = [tool.Child(ROOT_NAME, "sim", tmp_path)]
    inside = "外れ — 内側の module へ移す"
    groups = {
        (f"{ROOT_NAME}.sim.local", "sim_cluster"): inside,
        (f"{ROOT_NAME}.sim.local", "step_book"): inside,
    }
    added = {(f"{ROOT_NAME}.sim.local", "MODULE_TAGS"), (f"{ROOT_NAME}.sim.local", "room_of")}
    rows = [
        _row("sim.local", name, "other", f"sim/local.hy:{line}")
        for line, name in enumerate(("sim_cluster", "step_book", "MODULE_TAGS", "room_of"))
    ]
    used = frozenset(
        {(f"{ROOT_NAME}.sim.local", "sim_cluster"), (f"{ROOT_NAME}.sim.local", "room_of")}
    )

    def fate_of(chosen: frozenset[tuple[str, str]] | None) -> dict[str, str]:
        return {f.name: f.fate for f in tool.decide_names(rows, children, groups, added, chosen)}

    assert fate_of(used) == {
        "sim_cluster": "entry",
        "step_book": "underscore",
        "MODULE_TAGS": "underscore",
        "room_of": "entry",
    }
    # 渡さなければ今までどおり: 表の組の「外れ」は _ に、「足した名」は入口へ。
    assert fate_of(None) == {
        "sim_cluster": "underscore",
        "step_book": "underscore",
        "MODULE_TAGS": "entry",
        "room_of": "entry",
    }


def test_used_names_reads_an_outside_tree_and_skips_the_package_itself(tmp_path: Path) -> None:
    """外の木が子の古い path から読む名を集め、package の dir の下(package 自身の検)は数えない。"""
    repo = _write_repo(
        tmp_path / "repo",
        {
            f"packages/{PACKAGE}/pyproject.toml": FILES[f"packages/{PACKAGE}/pyproject.toml"],
            f"packages/{PACKAGE}/src/{ROOT_NAME}/__init__.py": "",
            f"packages/{PACKAGE}/src/{ROOT_NAME}/sim/__init__.py": "",
            f"packages/{PACKAGE}/src/{ROOT_NAME}/sim/local.py": "def run():\n    return 1\n",
            f"packages/{PACKAGE}/tests/test_local.py": f"from {ROOT_NAME}.sim.local import own\n",
            "callers/caller.py": f"from {ROOT_NAME}.sim.local import run\n",
            "callers/caller.hy": f"(import {ROOT_NAME}.sim.local [step-book])\n",
        },
    )
    package_dir = repo / "packages" / PACKAGE
    layout = tool._inventory_tool().read_layout(package_dir)
    children = tool.find_children(layout, ["sim"], hidden=False)
    assert tool.used_names(package_dir, layout, children, [repo]) == {
        (f"{ROOT_NAME}.sim.local", "run"),
        (f"{ROOT_NAME}.sim.local", "step_book"),
    }


def test_a_name_added_to_an_entry_with_all_is_listed_in_all_too(tmp_path: Path) -> None:
    """``__all__`` の在る入口へ名を足すと、``__all__`` にも ruff の RUF022 の並びで足す。1 行に 1 つの名でない
    ``__all__`` には足さず、子を移す前に止める。

    失敗ケース: 目録と ``import *`` は、``__all__`` の在る module では ``__all__`` の名だけを読む。doeff-events の入口は
    ``__all__`` を持ち、遅延の表だけに足した 11 名は入口の名と数えられなかった(``__all__`` の在る
    入口は 16 個)。
    """
    root_dir = tmp_path / ROOT_NAME
    root_dir.mkdir()
    entry = root_dir / "__init__.py"
    entry.write_text(
        textwrap.dedent('''
            """The fixture package."""

            from .effects import PingEffect as PingEffect

            __all__ = [
                "GAP",
                "PingEffect",
                "ping",
            ]
        ''').lstrip("\n"),
        encoding="utf-8",
    )
    shape = tool.EntryShape.read(entry)
    assert shape.problem() is None
    fates = [
        tool.NameFate(
            name,
            (f"{ROOT_NAME}.handlers",),
            "other",
            None,
            "entry",
            source=f"{ROOT_NAME}._handlers",
        )
        for name in ("ping_handler", "PingConfig", "MAX_PINGS", "T")
    ]

    added, _skipped = tool.update_entry(shape, ROOT_NAME, fates, [], root_dir)

    assert sorted(added) == ["MAX_PINGS", "PingConfig", "T", "ping_handler"]
    listed = next(
        node.value
        for node in ast.parse(entry.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
    )
    assert ast.literal_eval(listed) == [
        "GAP",
        "MAX_PINGS",
        "PingConfig",
        "PingEffect",
        "T",
        "ping",
        "ping_handler",
    ]
    inline = tmp_path / "inline" / "__init__.py"
    inline.parent.mkdir()
    inline.write_text('__all__ = ["PingEffect"]\n', encoding="utf-8")
    assert "__all__" in (tool.EntryShape.read(inline).problem() or "")


WRAPPED_PACKAGE = "doeff-fixture-wrapped"
WRAPPED_ROOT = "doeff_fixture_wrapped"

#: 入口が子を即座に読んで ``__all__`` で名を出し、handler を ``@handler`` で包んだ package(包んだ物の定義の module は
#: 包みの側の module になる)。
WRAPPED_FILES = {
    f"packages/{WRAPPED_PACKAGE}/pyproject.toml": f"""
        [project]
        name = "{WRAPPED_PACKAGE}"
        version = "0"

        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [tool.hatch.build.targets.wheel]
        packages = ["src/{WRAPPED_ROOT}"]
    """,
    f"packages/{WRAPPED_PACKAGE}/src/{WRAPPED_ROOT}/__init__.py": '''
        """The fixture package: the entry reads its child at once and names the names in __all__."""

        from .handlers import PingEffect, mock_ping_handler, ping_handler

        __all__ = [
            "PingEffect",
            "mock_ping_handler",
            "ping_handler",
        ]
    ''',
    f"packages/{WRAPPED_PACKAGE}/src/{WRAPPED_ROOT}/handlers/__init__.py": """
        from .production import PingEffect, ping_handler
        from .testing import mock_ping_handler
    """,
    f"packages/{WRAPPED_PACKAGE}/src/{WRAPPED_ROOT}/handlers/production.py": '''
        from doeff import EffectBase, Pass, handler


        class PingEffect(EffectBase):
            """The one effect."""


        @handler
        def ping_handler(effect: PingEffect, k):
            yield Pass()
    ''',
    f"packages/{WRAPPED_PACKAGE}/src/{WRAPPED_ROOT}/handlers/testing.py": f"""
        from doeff import Pass, handler

        from {WRAPPED_ROOT}.handlers.production import PingEffect


        @handler
        def mock_ping_handler(effect: PingEffect, k):
            yield Pass()
    """,
    "tests/data/public_api_known_violations.json": json.dumps(
        {
            "packages": {
                WRAPPED_PACKAGE: {
                    "範囲": "決まりの範囲",
                    "名": {
                        "外れ — submodule を _ で隠す": {
                            "(submodule)": [
                                f"{WRAPPED_ROOT}.handlers",
                                f"{WRAPPED_ROOT}.handlers.production",
                                f"{WRAPPED_ROOT}.handlers.testing",
                            ]
                        },
                    },
                }
            },
            "足した名": [],
        },
        ensure_ascii=False,
        indent=2,
    ),
    "callers/pytest.ini": "[pytest]\n",
    "callers/test_callers.py": f"""
        from {WRAPPED_ROOT}.handlers import mock_ping_handler, ping_handler
        from {WRAPPED_ROOT}.handlers.production import PingEffect


        def test_names_read_through_the_imports():
            assert isinstance(PingEffect, type)
            assert ping_handler is not mock_ping_handler
    """,
}


@pytest.mark.timeout(600)
def test_hide_points_callers_at_the_entry_for_wrapped_names_in_the_entry_all(
    tmp_path: Path,
) -> None:
    """入口の ``__all__`` に在る名は、``@handler`` で包んで定義の module が包みの側になった物でも入口から読ませる。

    前は入口が出す名を、遅延の表・``X as X``・package の中で定義した物だけと読み、``__all__`` で出す包んだ handler の
    呼び手を _ の path へ向けていた(doeff-gemini の ``default_gemini_cost_handler`` の定義の
    module は ``doeff.program``)。
    """
    repo = _write_repo(tmp_path / "repo", WRAPPED_FILES)
    src = repo / "packages" / WRAPPED_PACKAGE / "src"
    _run_callers(repo / "callers", src)

    report = tool.hide(repo / "packages" / WRAPPED_PACKAGE, ["handlers"], None, None, None, [])

    callers = _text(repo, "callers/test_callers.py")
    assert f"from {WRAPPED_ROOT} import mock_ping_handler, ping_handler\n" in callers
    assert f"from {WRAPPED_ROOT} import PingEffect\n" in callers
    assert report.rewrite.names_to_entry == 3
    assert report.rewrite.names_to_underscore == 0
    _run_callers(repo / "callers", src)


@pytest.mark.timeout(600)
def test_hide_rewrite_and_finish_keep_the_python_and_hy_callers_green(tmp_path: Path) -> None:
    """hide・rewrite・finish のどの後でも、doeff の木と外の repo の呼び手の test が緑のまま。"""
    repo = _write_repo(
        tmp_path / "repo", {**FILES, **{f"callers/{k}": v for k, v in CALLERS.items()}}
    )
    outside = _write_repo(tmp_path / "outside", {f"ext/{k}": v for k, v in CALLERS.items()})
    package = repo / "packages" / PACKAGE
    src = package / "src"
    package_root = src / ROOT_NAME
    record_before = _text(repo, "docs/design/record.md")
    _check_table(repo)
    _run_callers(repo / "callers", src)
    _run_callers(outside / "ext", src)

    # stage 1
    report = tool.hide(package, ["effects", "tools"], None, None, None, [])
    _assert_moved_and_entry(repo, report)
    _assert_rewritten(repo, report, record_before)
    _check_table(repo)  # 移し替えの module が古い名で出す間は、表を書き換えずに緑
    # 移し替えの module は印の下に、古い path が出す名の固定の列を持つ。
    assert f"{tool.SHIM_NAMES_MARK} {ROOT_NAME}.effects:" in _text(
        repo, f"packages/{PACKAGE}/src/{ROOT_NAME}/effects.py"
    )
    # 目録の古い綴りの行は書き換えずに残り、前に _ の綴りの行(入口が出す名は入口の綴りの行も)が足る。
    assert _catalog_spellings(repo) == [
        f"{ROOT_NAME}._effects:ping-handler",
        f"{ROOT_NAME}:ping_handler",
        f"{ROOT_NAME}.effects:ping-handler",
        f"{ROOT_NAME}._tools.text:shout",
        f"{ROOT_NAME}.tools.text:shout",
        "elsewhere.io:real-handler",
    ]
    assert report.catalog_added == (
        f"{ROOT_NAME}._effects:ping-handler",
        f"{ROOT_NAME}:ping_handler",
        f"{ROOT_NAME}._tools.text:shout",
    )
    assert _catalog_missing_old_spellings(repo) == []
    _run_callers(repo / "callers", src)
    _run_callers(outside / "ext", src)  # 外の repo は古い path のまま移し替えの module を通る

    # stage 2
    tool.rewrite(package, ["effects", "tools"], outside, None, None, [], False)
    assert f"from {ROOT_NAME} import PingConfig, PingEffect\n" in _text(
        outside, "ext/test_callers.py"
    )
    _run_callers(outside / "ext", src)

    # stage 3
    finished = tool.finish(package, ["effects", "tools"], None, None, None)
    assert not (package_root / "effects.py").exists()
    assert not (package_root / "tools.py").exists()
    assert sorted(finished.removed) == [
        f"packages/{PACKAGE}/src/{ROOT_NAME}/effects.py",
        f"packages/{PACKAGE}/src/{ROOT_NAME}/tools.py",
    ]
    assert finished.remaining == {"docs/guide.md": 1}
    # 消えた古い名の行(隠した submodule・_ に残した外れ・Hy の macro)を消し、入口へ移した PingConfig の行は残す。
    assert finished.table_rows_removed == {
        "外れ — submodule を _ で隠す": 4,
        "外れ — 内側の module へ移す": 3,
        "境界 — Hy の macro": 1,
    }
    assert finished.table_rows_left == {"境界 — handler の設定の型": 1}
    # 移し替えの module を消したので、目録の古い綴りの行も消える(載せない物の注の鍵は _ の綴りへ)。
    assert _catalog_spellings(repo) == [
        f"{ROOT_NAME}._effects:ping-handler",
        f"{ROOT_NAME}:ping_handler",
        f"{ROOT_NAME}._tools.text:shout",
        "elsewhere.io:real-handler",
    ]
    assert finished.catalog_removed == (
        f"{ROOT_NAME}.effects:ping-handler",
        f"{ROOT_NAME}.tools.text:shout",
    )
    assert list(json.loads(_text(repo, CATALOG))["_not_listed"]) == [
        f"{ROOT_NAME}._effects:call_helper"
    ]
    _check_table(repo)
    _run_callers(repo / "callers", src)
    _run_callers(outside / "ext", src)


def test_finish_renames_an_old_catalog_row_without_its_underscore_row(tmp_path: Path) -> None:
    """_ の綴りの行が無い古い綴りの行(道具の外で足した行)を、finish は消さずに _ の綴りへ書き換える。"""
    path = tmp_path / "world_handlers.json"
    path.write_text(
        json.dumps(
            {
                "handlers": [
                    {"handler": f"{ROOT_NAME}.effects:ping-handler", "touches": ["file"], "why": "a"},
                    {"handler": f"{ROOT_NAME}.effects:pong-handler", "touches": ["file"], "why": "b"},
                    {"handler": f"{ROOT_NAME}._effects:pong-handler", "touches": ["file"], "why": "b"},
                ]
            }
        ),
        encoding="utf-8",
    )
    removed, notes = tool.finish_catalog(path, [tool.Child(ROOT_NAME, "effects", tmp_path)])
    assert removed == (f"{ROOT_NAME}.effects:pong-handler",)
    assert [row["handler"] for row in json.loads(path.read_text(encoding="utf-8"))["handlers"]] == [
        f"{ROOT_NAME}._effects:ping-handler",
        f"{ROOT_NAME}._effects:pong-handler",
    ]
    assert len(notes) == 1
    assert f"{ROOT_NAME}.effects:ping-handler → {ROOT_NAME}._effects:ping-handler" in notes[0]


CHAIN_PACKAGE = "doeff-fixture-hide-chain"
CHAIN_ROOT = "doeff_fixture_hide_chain"

#: 子の package の __init__ と根の __init__ が、子の下の module を古い絶対の path で読む package(doeff_time の形)。
CHAIN_FILES = {
    f"packages/{CHAIN_PACKAGE}/pyproject.toml": f"""
        [project]
        name = "{CHAIN_PACKAGE}"
        version = "0"

        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [tool.hatch.build.targets.wheel]
        packages = ["src/{CHAIN_ROOT}"]
    """,
    f"packages/{CHAIN_PACKAGE}/src/{CHAIN_ROOT}/__init__.py": f'''
        """The fixture package whose child package reads its own module by the old path."""

        from {CHAIN_ROOT}.clock.ticks import Tick as Tick
    ''',
    f"packages/{CHAIN_PACKAGE}/src/{CHAIN_ROOT}/clock/__init__.py": f"""
        from {CHAIN_ROOT}.clock.ticks import Tick as Tick
    """,
    f"packages/{CHAIN_PACKAGE}/src/{CHAIN_ROOT}/clock/ticks.py": '''
        from doeff import EffectBase


        class Tick(EffectBase):
            """The one effect."""
    ''',
    "callers/caller.py": f"""
        from {CHAIN_ROOT}.clock.ticks import Tick
    """,
}


@pytest.mark.timeout(600)
def test_hide_points_a_caller_at_the_entry_when_the_child_reads_itself_by_the_old_path(
    tmp_path: Path,
) -> None:
    """子の package の中が子を古い path で読んでも、入口と同じ名を読む呼び手は入口から読む形へ書き換わる。

    入口と比べる子の process が根を読む前に根の中の import を書き換えないと、``_clock/__init__`` の古い path の
    import が package でない移し替えの module に当たって落ち、呼び手が ``_`` の path へ向いた。
    """
    repo = _write_repo(tmp_path / "repo", CHAIN_FILES)
    report = tool.hide(repo / "packages" / CHAIN_PACKAGE, ["clock"], None, None, None, [])
    assert _text(repo, "callers/caller.py") == f"from {CHAIN_ROOT} import Tick\n"
    assert (report.rewrite.names_to_entry, report.rewrite.names_to_underscore) == (1, 0)
    assert f"from {CHAIN_ROOT}._clock.ticks import Tick as Tick" in _text(
        repo, f"packages/{CHAIN_PACKAGE}/src/{CHAIN_ROOT}/_clock/__init__.py"
    )


def test_from_root_import_keeps_a_name_the_entry_binds_to_another_object(tmp_path: Path) -> None:
    """入口が子と同じ名で別の物を出す時(doeff_openrouter の関数 structured_llm)、``from <根> import <名>`` は入口の物を読む。

    子 ``structured_llm`` と ``handlers`` を隠した後、入口は ``from ._structured_llm import structured_llm``(関数)と
    ``from . import _handlers as handlers``(子の module)を持つ。呼び手の ``structured_llm`` は関数のまま残し、
    ``handlers`` だけを ``_handlers as handlers`` へ書き換える(前に README を誤って書き換えた)。
    """
    root_dir = tmp_path / "pkg"
    root_dir.mkdir()
    (root_dir / "__init__.py").write_text(
        "from . import _handlers as handlers\nfrom ._structured_llm import structured_llm\n",
        encoding="utf-8",
    )
    patterns = tool.Patterns.of(
        [tool.Child("pkg", "structured_llm", root_dir), tool.Child("pkg", "handlers", root_dir)]
    )
    context = tool.FileContext("caller.py", None, False)
    python, _ = tool.rewrite_python(
        "from pkg import handlers, structured_llm\n", patterns, context, lambda _module, _name: True
    )
    assert python == "from pkg import _handlers as handlers, structured_llm\n"
    hy, _ = tool.rewrite_hy(
        "(import pkg [handlers structured-llm])\n", patterns, context, lambda _module, _name: True
    )
    assert hy == "(import pkg [_handlers :as handlers structured-llm])\n"


def test_a_relative_hy_import_is_left_as_it_is(tmp_path: Path) -> None:
    """Hy の相対の import(doeff_cluster の ``(import .runtime_env_model [EnvVar])``)を読んでも落ちず、書き換えない。

    頭の点の空の名を ``hy.mangle`` へ渡して hide が AssertionError で止まった。
    """
    root_dir = tmp_path / "pkg"
    root_dir.mkdir()
    patterns = tool.Patterns.of([tool.Child("pkg", "handlers", root_dir)])
    context = tool.FileContext("other/model.hy", None, False)
    text = "(import .runtime-env-model [EnvVar])\n(import pkg.handlers [h])\n"
    hy, _ = tool.rewrite_hy(text, patterns, context, lambda _module, _name: True)
    assert hy == "(import .runtime-env-model [EnvVar])\n(import pkg [h])\n"


CLASH_PACKAGE = "doeff-fixture-hide-clash"
CLASH_ROOT = "doeff_fixture_hide_clash"

#: 子の名が入口の出す名と同じ package: 子 ping_handler は同じ名の handler を出し(入口へ足す名)、子 tool は入口が既に
#: 同じ名の関数に束ねる(入口が既に束ねる名 — 移す前の doeff_openrouter の structured_llm の形)。
CLASH_FILES = {
    f"packages/{CLASH_PACKAGE}/pyproject.toml": f"""
        [project]
        name = "{CLASH_PACKAGE}"
        version = "0"

        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [tool.hatch.build.targets.wheel]
        packages = ["src/{CLASH_ROOT}"]
    """,
    f"packages/{CLASH_PACKAGE}/src/{CLASH_ROOT}/__init__.py": f'''
        """The fixture package whose children are named like names its entry shows."""

        from {CLASH_ROOT}.tool import tool as tool
    ''',
    f"packages/{CLASH_PACKAGE}/src/{CLASH_ROOT}/tool.py": """
        def tool() -> str:
            return "entry"
    """,
    f"packages/{CLASH_PACKAGE}/src/{CLASH_ROOT}/ping_handler.py": """
        from doeff import EffectBase, Pass, handler


        class Ping(EffectBase):
            \"\"\"The one effect.\"\"\"


        @handler
        def ping_handler(effect: Ping, k):
            yield Pass()
    """,
    f"packages/{CLASH_PACKAGE}/src/{CLASH_ROOT}/plain.py": """
        VALUE = 1
    """,
}


@pytest.mark.timeout(600)
@pytest.mark.parametrize(
    ("child", "why"),
    [("ping_handler", "入口へ足す名"), ("tool", "入口が既に束ねる名")],
)
def test_hide_refuses_a_child_named_like_a_name_the_entry_shows(
    tmp_path: Path, child: str, why: str
) -> None:
    """子の名が入口の出す名と同じなら、hide は移し替えの module を置かずに断り、木に何も書かない(ADR-DOE-API-001 R34)。

    古い path の import が親の属性を module に束ね、入口の名を隠す(doeff_core_effects の
    aiohttp_http_server の殻で記録の service が起動の度に落ちた)。前の道具は入口へ足す名を見ずに殻を置き、入口が既に
    束ねる名では package の class を差し替える手当てを殻に書いた。同じ名でない子(plain)は今までどおり隠せる。
    """
    repo = _write_repo(tmp_path / "repo", CLASH_FILES)
    package_dir = repo / "packages" / CLASH_PACKAGE
    with pytest.raises(SystemExit) as refused:
        tool.hide(package_dir, [child, "plain"], None, None, None, [])
    assert f"{CLASH_ROOT}.{child}: 入口 {CLASH_ROOT} が同じ名 {child} を出す" in str(refused.value), why
    assert "ADR-DOE-API-001 R34" in str(refused.value)
    assert "plain" not in str(refused.value)
    assert _git(repo, "status", "--porcelain") == ""
    report = tool.hide(package_dir, ["plain"], None, None, None, [])
    assert [c["new"] for c in report.children] == [f"{CLASH_ROOT}._plain"]
    assert (package_dir / "src" / CLASH_ROOT / "plain.py").read_text(encoding="utf-8").startswith(
        f"{tool.SHIM_MARK} {CLASH_ROOT}.plain -> {CLASH_ROOT}._plain\n"
    )


@pytest.mark.timeout(600)
def test_hide_holds_a_name_like_its_child_inside_until_the_shim_is_gone(tmp_path: Path) -> None:
    """``--hold`` の名は入口へ出さず ``_`` に残し、移し替えの module を置いて隠す — R34 の順の 1 段目。

    子 ping_handler の handler ping_handler は入口へ出す種類なので、hold が無ければ R34 で断る。hold すると入口は同じ名を
    出さず(古い path の import が入口の名を module で隠す事が起きない)、同じ子の他の名(effect Ping)は入口へ出す。
    子の名でない名と、入口が既に束ねる名(tool)は hold できない。
    """
    repo = _write_repo(tmp_path / "repo", CLASH_FILES)
    package_dir = repo / "packages" / CLASH_PACKAGE
    with pytest.raises(SystemExit) as stranger:
        tool.hide(package_dir, ["ping_handler"], None, None, None, [], hold=["Ping"])
    assert "--hold は子と同じ名だけ" in str(stranger.value)
    with pytest.raises(SystemExit) as bound:
        tool.hide(package_dir, ["tool"], None, None, None, [], hold=["tool"])
    assert "ADR-DOE-API-001 R34" in str(bound.value)
    assert _git(repo, "status", "--porcelain") == ""
    report = tool.hide(package_dir, ["ping_handler"], None, None, None, [], hold=["ping_handler"])
    src = package_dir / "src" / CLASH_ROOT
    assert [c["new"] for c in report.children] == [f"{CLASH_ROOT}._ping_handler"]
    assert (src / "ping_handler.py").read_text(encoding="utf-8").startswith(
        f"{tool.SHIM_MARK} {CLASH_ROOT}.ping_handler -> {CLASH_ROOT}._ping_handler\n"
    )
    assert report.entry_added == ("Ping",)
    assert report.names["_ に残す"] == [f"{CLASH_ROOT}.ping_handler.ping_handler"]
    assert f"{tool.HELD_NOTE}を _ に残した: {CLASH_ROOT}.ping_handler.ping_handler" in report.notes
    assert '"ping_handler"' not in (src / "__init__.py").read_text(encoding="utf-8")


def test_a_shim_does_not_guard_the_entry_name(tmp_path: Path) -> None:
    """移し替えの module は package の class を差し替えない — 入口の名を守る手当ては無く、同じ名の子は hide が断る(R34)。

    前の道具は、入口が子と同じ名を別の物に束ねる時(doeff_openrouter の structured_llm)、殻に package の ``__class__`` を
    差し替えて module の束ねを捨てる手当てを書いた。手当ては古い path の import の順を隠すだけで、衝突は残った。
    """
    root_dir = tmp_path / "kept_pkg"
    root_dir.mkdir()
    (root_dir / "__init__.py").write_text("from ._tool import tool\n", encoding="utf-8")
    text = tool.shim_text(tool.Child("kept_pkg", "tool", root_dir), False, {})
    assert "__class__" not in text
    assert "__setattr__" not in text


def test_pin_writes_the_names_into_the_shim_of_a_namespace_dir(tmp_path: Path) -> None:
    """namespace の dir(__init__ の無い _area)を指す移し替えの module にも、pin が下の古い path の名の固定の列を書く。

    その移し替えの module は module でないので、目録の module の列だけから移し替えの module を探すと拾わず、何も書かな
    かった(doeff-cluster の sim.py)。列には effect・handler と既知の表の名だけを書き、書き手が
    下に足した helper は書かない。
    """
    src = f"packages/{PACKAGE}/src/{ROOT_NAME}"
    repo = _write_repo(
        tmp_path / "repo",
        {
            f"packages/{PACKAGE}/pyproject.toml": FILES[f"packages/{PACKAGE}/pyproject.toml"],
            f"{src}/__init__.py": '"""The fixture package."""\n',
            f"{src}/_area/sub.py": '''
                from doeff import EffectBase


                class PingEffect(EffectBase):
                    """The one effect."""


                def helper(x: int) -> int:
                    return x + 1
            ''',
        },
    )
    child = tool.Child(ROOT_NAME, "area", repo / src)
    child.shim.write_text(tool.shim_text(child, False, {}), encoding="utf-8")
    report = tool.pin(repo / "packages" / PACKAGE, None, None)
    head = child.shim.read_text(encoding="utf-8").split('"""', 1)[0]
    assert head == (
        f"{tool.SHIM_MARK} {child.old} -> {child.new}\n"
        f"{tool.SHIM_NAMES_MARK} {child.old}.sub:\n"
        f"{tool.SHIM_NAMES_MORE} PingEffect\n"
    )
    assert report.names == {
        "列に書いた名": [f"{child.old}.sub.PingEffect"],
        "列から外した名": [f"{child.old}.sub.helper"],
    }


@pytest.mark.timeout(300)
def test_hy_m_on_an_old_path_runs_the_moved_hy_module_while_the_bytecode_guard_compiles(tmp_path: Path) -> None:
    """古い path の ``hy -m <old>.<sub>`` が、移した Hy の module を __main__ として走らせる — .pyc も保存先の code も無く、
    bytecode guard が移した module とそれが読む Hy の module を compile する時にも。

    殻の -m の道が runpy.run_module を入れ子に呼んでいた時、外の run が file の無い __main__ を sys.modules に置いたまま中の
    run が移した Hy の module を compile し、inspect.getmodule が TypeError を出した(本番の moneta-ledger と moneta-view が、
    古い worker の送る子の入口 ``hy -m doeff_cluster.worker.entry.job_entry`` で起動のたびに落ちた — 2026-10-31・agora card
    ki-eb15f6bdadfa)。__main__ が走る間に compile する late は、__main__ の __file__ が None の時に同じ形で落ちる。
    """
    src = tmp_path / "src" / "fixmoved"
    _write_repo(
        tmp_path / "src",
        {
            "fixmoved/__init__.py": "",
            # The package compiles a Hy module before __main__ is swapped, so inspect.getmodule has read the first __main__.
            "fixmoved/_jobs/__init__.py": """
                import hy  # noqa: F401 - lets the Hy modules below be imported

                from fixmoved._jobs.helper import greet as greet
            """,
            "fixmoved/_jobs/helper.hy": '(defn greet [] "hello")\n',
            "fixmoved/_jobs/late.hy": '(defn later [] "from the moved module")\n',
            "fixmoved/_jobs/entry.hy": """
                (import sys)
                (import fixmoved._jobs [greet])
                (import fixmoved._jobs.late [later])

                (when (= __name__ "__main__")
                  (print (greet) (later))
                  (print __file__)
                  (print (get sys.argv 0)))
            """,
        },
    )
    child = tool.Child("fixmoved", "jobs", src)
    child.shim.write_text(tool.shim_text(child, False, {}), encoding="utf-8")
    environment = {
        **os.environ,
        "PYTHONPATH": str(tmp_path / "src"),
        "DOEFF_HY_CODE_STORE": str(tmp_path / "store"),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
    }

    finished = subprocess.run(
        [sys.executable, "-m", "hy", "-m", "fixmoved.jobs.entry"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr
    moved = str(src / "_jobs" / "entry.hy")
    assert finished.stdout.splitlines() == ["hello from the moved module", moved, moved]


@pytest.mark.parametrize(
    ("shim", "old"),
    [
        ("packages/doeff-cluster/src/doeff_cluster/worker.py", "doeff_cluster.worker"),
        ("packages/doeff-cluster/src/doeff_cluster/shared.py", "doeff_cluster.shared"),
        ("packages/doeff-core-effects/doeff_core_effects/storage.py", "doeff_core_effects.storage"),
    ],
)
def test_a_committed_package_shim_carries_the_finder_the_tool_writes(shim: str, old: str) -> None:
    """木に在る package の殻の finder は、道具の書く finder と同じ字面 — 道具の finder を直した時に、置いた殻も直っている事
    (上の test は道具の書く殻で -m の道を確かめる)。"""
    root, name = old.rsplit(".", 1)
    finder = tool._SHIM_FINDER.format(old=old, new=f"{root}._{name}")

    assert finder in (ROOT / shim).read_text(encoding="utf-8")
