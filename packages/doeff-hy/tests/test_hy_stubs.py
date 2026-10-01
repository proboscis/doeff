"""doeff-hy が配る hy の部分の stub(src/hy-stubs)の失敗ケース(agora-redesign #2313)。

hy は型の宣言を持たない。Hy の展開は keyword の literal と quote した form を `hy.models.Keyword(…)` などに compile し、
doeff-hy-check はそれを読む module に `import hy.models` を置くので、前は新しい .hy の file ごとに必ず
`Stub file not found for "hy.models"`(と "hy")が strict の赤で出ていた(書き手に直せない — agora の基点に 758 file)。

- hy の名と model を使う小さな .hy に、hy / hy.models の reportMissingTypeStubs も「型が分からない」の赤も出ない。
- stub が宣言する名は実行時の hy に在る(宣言だけが先へ行かない)・stub は partial(宣言の無い下の module は hy の source)。
"""

import ast
import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import hy
import hy.models
import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

STUBS = Path(doeff_hy.__file__).parent.parent / "hy-stubs"

MODULE = """\
(require doeff-hy.macros [val])
(import hy.models [Keyword Symbol])

(val KEYS [:a :b])
(val FORM (quote (f "x" 1 2.5)))
(val HEAD (str (get FORM 0)))
(val NAME (+ (. (Keyword "k") name) (hy.mangle "a-b")))
(val SYMBOL (Symbol "s"))
"""


@dataclass(frozen=True)
class Run:
    """doeff-hy-check を 1 回走らせた結果(JSON の診断)。"""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _check(root: Path) -> Run:
    from doeff_hy.static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_a_new_hy_file_has_no_missing_hy_stub(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if e[0] == "reportMissingTypeStubs"], errors
    assert not [e for e in errors if e[0].startswith("reportUnknown")], errors


def _names_of(node: ast.stmt) -> tuple[str, ...]:
    """stub の直下の文 1 つが宣言する名(再公開は `as` の名)。"""
    match node:
        case ast.ClassDef(name=name) | ast.FunctionDef(name=name) | ast.AnnAssign(target=ast.Name(id=name)):
            return (name,)
        case ast.ImportFrom(names=aliases):
            return tuple(a.asname for a in aliases if a.asname is not None)
        case _:
            return ()


def _declared(stub: Path) -> list[str]:
    """stub の module の直下で宣言した公開の名(_ で始まる名は stub の中だけの型)。"""
    tree = ast.parse(stub.read_text(encoding="utf-8"))
    return [
        n for node in tree.body for n in _names_of(node) if not n.startswith("_") or n.startswith("__")
    ]


def test_the_stub_names_exist_in_hy() -> None:
    assert [n for n in _declared(STUBS / "__init__.pyi") if not hasattr(hy, n)] == []
    assert [n for n in _declared(STUBS / "models.pyi") if not hasattr(hy.models, n)] == []
    # 宣言の無い下の module(hy.compiler など)は hy の source から読ませる。
    assert (STUBS / "py.typed").read_text(encoding="utf-8").strip() == "partial"
