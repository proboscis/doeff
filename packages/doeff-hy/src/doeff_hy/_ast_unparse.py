"""Hy が差し替えた `ast.unparse` を、AST の定数の値を複製しない同じ変換へ置き換える。

起きていたこと(2026-09-23 実測・Python 3.14.3・hy 1.3.0 / 上流 master も同じ):
`from __future__ import annotations` の無い conftest.py に `Callable[[Program], object]`
のような注記の fixture があると、pytest が止まらずメモリを 10 GB まで食った。

仕組み:
- Hy の `hy/compat.py` は import された時に `ast.unparse` を process 全体で
  `rewriting_unparse` へ差し替える(Python の予約語の名前を別の文字へ写す「keyword
  mincing」のため)。`rewriting_unparse` は受け取った AST を `copy.deepcopy` してから写す。
- pytest 9 は 3.14 で `inspect.signature(fixture, annotation_format=Format.STRING)` を呼ぶ。
  `annotationlib` は STRING 形式で注記を評価する時、名前を `_Stringifier` にし、添字の中の
  list などを `ast.Constant(value=[<_Stringifier>])` へそのまま入れる(Constant の値に
  任意の object が入る)。
- `deepcopy` がその `_Stringifier` に `__deepcopy__` を問い合わせると、`_Stringifier` は
  どの属性にも新しい `_Stringifier`(AST を 1 段足した物)を返し、呼ぶと memo の dict まで
  抱えた AST になる。それを文字列にするとまた `ast.unparse` = `rewriting_unparse` が呼ばれ、
  もっと大きな木を deepcopy する — 再帰と木の成長が止まらない。
- `from __future__ import annotations` があると注記は最初から文字列で、`_Stringifier` を
  通らないので起きない。

直し方: 写す対象は AST の node の欄(名前の文字列)だけで、`ast.Constant` の値には触らない
(Hy 自身も「文字列の定数は写さない」)。だから複製するのは node だけでよい。
`copy.deepcopy` の memo に Constant の値を自分自身として先に入れ、値を複製しないで node だけ
複製する。変換(keyword mincing)と最後に呼ぶ本来の `ast.unparse` は Hy と同じ。

⚠ これは Hy 本体の欠陥への doeff-hy 側の当て物である。根は Hy が `ast.unparse` を process
全体で差し替えること(`annotationlib` のような他の利用者も巻き込む)と、差し替えた関数が
任意の値を deepcopy すること。上流の報告の文面 = `packages/doeff-hy/docs/hy-upstream-rewriting-unparse.md`。
上流で直った版の Hy では、`hy.compat.rewriting_unparse` が無いか `ast.unparse` が別物に
なっているので、ここは何もしない(`install` の条件)。
"""

import ast
import copy
import keyword
from collections.abc import Callable

_KEEP = ("True", "False", "None")


_MINCED_A = 0x1D41A  # MATHEMATICAL BOLD SMALL A(Hy と同じ写し先)


def _mince(name: str) -> str:
    return chr(ord(name[0]) - ord("a") + _MINCED_A) + name[1:]


def unparse_without_copying_constants(
    true_unparse: Callable[[ast.AST], str],
) -> Callable[[ast.AST], str]:
    """Hy の `rewriting_unparse` と同じ変換で、Constant の値を複製しない関数を作る。"""

    def minced_unparse(ast_obj: ast.AST) -> str:
        constants: dict[int, object] = {
            id(node.value): node.value
            for node in ast.walk(ast_obj)
            if isinstance(node, ast.Constant)
        }
        copied = copy.deepcopy(ast_obj, constants)
        for node in ast.walk(copied):
            if type(node) is ast.Constant:
                continue
            present = vars(node)
            for field in node._fields:
                value = present.get(field)
                if type(value) is str and keyword.iskeyword(value) and value not in _KEEP:
                    setattr(node, field, _mince(value))
        return true_unparse(copied)

    return minced_unparse


def install() -> bool:
    """Hy の差し替えが入っていれば置き換える。置き換えたら True。何度呼んでもよい。"""
    import hy.compat

    rewriting = hy.compat.__dict__.get("rewriting_unparse")
    true_unparse = hy.compat.__dict__.get("true_unparse")
    if rewriting is None or true_unparse is None or ast.unparse is not rewriting:
        return False
    replacement = unparse_without_copying_constants(true_unparse)
    ast.unparse = replacement
    hy.compat.rewriting_unparse = replacement
    return True
