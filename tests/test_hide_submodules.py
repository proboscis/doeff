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
    report = tool.hide(package, ["effects", "tools"], None, None, [])
    _assert_moved_and_entry(repo, report)
    _assert_rewritten(repo, report, record_before)
    _check_table(repo)  # 移し替えの module が古い名で出す間は、表を書き換えずに緑
    _run_callers(repo / "callers", src)
    _run_callers(outside / "ext", src)  # 外の repo は古い path のまま移し替えの module を通る

    # stage 2
    tool.rewrite(package, ["effects", "tools"], outside, None, [], False)
    assert f"from {ROOT_NAME} import PingConfig, PingEffect\n" in _text(
        outside, "ext/test_callers.py"
    )
    _run_callers(outside / "ext", src)

    # stage 3
    finished = tool.finish(package, ["effects", "tools"], None, None)
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
    _check_table(repo)
    _run_callers(repo / "callers", src)
    _run_callers(outside / "ext", src)


@pytest.mark.timeout(600)
def test_hide_points_callers_at_an_entry_that_reads_its_children_by_the_old_path(
    tmp_path: Path,
) -> None:
    """入口が子を古い path で読み(子の ``__init__`` も自分の下の module を古い path で読む)、2 つの子が同じ定義の名を
    出し、入口に ``__all__`` が在っても、hide は名を入口へ 1 度だけ足し、呼び手を入口へ向ける(card ki-a883efd67a46 —
    doeff-events の形)。

    入口と比べる子の process は入口を import する。根の中の file を書き換える前に比べると、移し替えの module を読む途中で
    古い path の下の module が読めず(読み込みの途中の移し替えの module は package でない)、どの名も入口へ向かなかった。
    ``__all__`` の在る入口では、目録が ``__all__`` の名だけを数えるので、足した名を ``__all__`` にも足す。
    """
    files = {
        **FILES,
        f"packages/{PACKAGE}/src/{ROOT_NAME}/__init__.py": f'''
            """The fixture package."""

            from {ROOT_NAME}.effects import PingEffect as PingEffect
            from {ROOT_NAME}.tools import Shout as Shout

            __all__ = [
                "PingEffect",
                "Shout",
            ]
        ''',
        f"packages/{PACKAGE}/src/{ROOT_NAME}/handlers.py": f'''
            """The handler, read again from the effects module (one definition under a second child)."""

            from {ROOT_NAME}.effects import ping_handler
        ''',
    }
    repo = _write_repo(
        tmp_path / "repo", {**files, **{f"callers/{k}": v for k, v in CALLERS.items()}}
    )
    package = repo / "packages" / PACKAGE

    report = tool.hide(package, ["effects", "tools", "handlers"], None, None, [])

    assert report.conflicts == ()
    assert sorted(report.entry_added) == ["PingConfig", "ping_handler"]
    entry = _text(repo, f"packages/{PACKAGE}/src/{ROOT_NAME}/__init__.py")
    assert f'"ping_handler": "{ROOT_NAME}._effects"' in entry
    assert f"from {ROOT_NAME}._tools import Shout as Shout" in entry
    listed = next(
        node.value
        for node in ast.parse(entry).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
    )
    assert ast.literal_eval(listed) == ["PingConfig", "PingEffect", "Shout", "ping_handler"]
    shim = _text(repo, f"packages/{PACKAGE}/src/{ROOT_NAME}/effects.py")
    assert "import *" not in shim  # semgrep の doeff-no-star-imports
    assert f"    from {ROOT_NAME}._effects import PingEffect as PingEffect\n" in shim
    [package_row] = tool._inventory_tool().inventory(repo, [PACKAGE], 1, 100)
    assert {"PingConfig", "ping_handler"} <= {
        row["name"] for row in package_row["exports"] if row["module"] == ROOT_NAME
    }
    callers = _text(repo, "callers/test_callers.py")
    assert f"from {ROOT_NAME} import PingConfig, PingEffect\n" in callers
    assert f"from {ROOT_NAME}._effects import call_helper, helper\n" in callers
    assert f"(import {ROOT_NAME} [Shout]" in _text(repo, "callers/hy_caller.hy")
    _run_callers(repo / "callers", package / "src")
