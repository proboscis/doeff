"""doeff-hy-check(doeff_hy/static_check.py)が、1 file だけを検める時も、import する file の dir の隣の .hy を展開して
pyright に解かせること(agora-redesign #2898)。

script を `hy scripts/x.hy` で撃つと scripts/ が探し道の先頭に入るので、script は隣の module を top-level の名で読む
(`(import e2e_wire [...])`)。pyright もこの形を「import する file の dir から根へ向けて親の dir を探す」で解く。前の依存の
展開は import の根(repo の根と extraPaths)の下だけを探したので、1 file だけを検めると隣の .hy が展開されず、
`Import "b" could not be resolved` と、そこから来る Unknown の赤(`Type of "helper" is unknown`・`Argument type is unknown`)が
出た。dir ごと検めると隣の .hy も検める file なので出なかった — 同じ file の答えが検め方で分かれた。
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest

from doeff_hy.static_check import main

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

# 隣の module(script が top-level の名で読む側)。
SIBLING = """(require doeff-hy.macros [defk])

(defk helper [n]
  {:pre [(: n int)] :post [(: % int)]}
  "倍にするため。"
  (* n 2))
"""

# 隣の module から名を import し、その関数を 2 度呼ぶ script(#2898 の 2 つの形 — 新しい名の import と呼び出しの追加)。
SCRIPT = """(require doeff-hy.macros [defk <-])
(import b [helper])

(defk twice [n]
  {:pre [(: n int)] :post [(: % int)]}
  "2 度倍にするため。"
  (<- once (helper n))
  (<- again (helper once))
  again)
"""

# 標準ライブラリと同じ名の隣の .hy(pyright は標準ライブラリを先に解く — 隣を探すのは解けない時だけ)。
SHADOW = """(require doeff-hy.macros [defk])

(defk dumps [n]
  {:pre [(: n int)] :post [(: % int)]}
  "標準ライブラリと同じ名の隣の module の関数(読まれてはいけない)。"
  n)
"""

USES_STDLIB = """(require doeff-hy.macros [defk])
(import json)

(defk shown [n]
  {:pre [(: n int)] :post [(: % str)]}
  "数を JSON の文字列にするため。"
  (json.dumps n))
"""


def _checked(root: Path, *paths: str) -> list[dict[str, object]]:
    """root の下の paths(file か dir)を strict で検め、--json の答えを読むため。"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--strict", "--json", "--no-cache", *(str(root / p) for p in paths)])
    return json.loads(out.getvalue())


def _errors_of(diagnostics: list[dict[str, object]], path: str) -> list[tuple[object, object, object]]:
    """1 つの file の赤を (行・規則・文言) で並べるため(検め方の違う 2 つの答えを比べる)。"""
    return sorted(
        (d["line"], d["rule"], d["message"])
        for d in diagnostics
        if d["path"] == path and d["severity"] == "error"
    )


def _repo(root: Path, files: dict[str, str]) -> Path:
    """strict の pyright の設定と、files(根からの相対 path → 中身)を置いた検体の根を作るため。"""
    (root / "pyproject.toml").write_text('[tool.pyright]\ntypeCheckingMode = "strict"\n', encoding="utf-8")
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_search_dirs_put_the_import_roots_first_then_the_file_dir_up_to_the_root(tmp_path: Path) -> None:
    from doeff_hy.static_check import search_dirs

    root = tmp_path / "repo"
    extra = root / "clients" / "hy"
    source = root / "scripts" / "deep" / "x.hy"
    assert search_dirs(root, [root, extra], source) == [root, extra, root / "scripts" / "deep", root / "scripts"]
    # 根の直下の file は根だけ(同じ dir を 2 度探さない)・根の外へは上がらない。
    assert search_dirs(root, [root], root / "x.hy") == [root]


@needs_pyright
def test_a_single_file_resolves_its_sibling_module_like_the_whole_dir(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/b.hy": SIBLING, "scripts/a.hy": SCRIPT})
    alone = _errors_of(_checked(root, "scripts/a.hy"), "scripts/a.hy")
    whole = _errors_of(_checked(root, "scripts"), "scripts/a.hy")
    assert alone == whole == [], (alone, whole)


@needs_pyright
def test_a_sibling_named_like_the_standard_library_does_not_shadow_it(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"scripts/json.hy": SHADOW, "scripts/a.hy": USES_STDLIB})
    alone = _errors_of(_checked(root, "scripts/a.hy"), "scripts/a.hy")
    whole = _errors_of(_checked(root, "scripts"), "scripts/a.hy")
    assert alone == whole == [], (alone, whole)
