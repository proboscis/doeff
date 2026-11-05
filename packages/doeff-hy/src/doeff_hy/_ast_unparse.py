"""Replace the `ast.unparse` that Hy swapped in with an equivalent transform that does not copy AST constant values.

What was happening (measured 2026-09-23, Python 3.14.3, hy 1.3.0 / upstream master behaves the same):
When a conftest.py without `from __future__ import annotations` has a fixture with an annotation such as
`Callable[[Program], object]`, pytest did not stop and ate memory up to 10 GB.

Mechanism:
- Hy's `hy/compat.py` replaces `ast.unparse` process-wide with `rewriting_unparse` when it is imported
  (to map Python reserved-word names to other characters, "keyword
  mincing"). `rewriting_unparse` runs `copy.deepcopy` on the AST it receives before mapping it.
- On 3.14, pytest 9 calls `inspect.signature(fixture, annotation_format=Format.STRING)`.
  When `annotationlib` evaluates annotations in STRING format, it turns names into `_Stringifier` and puts the list etc. inside a subscript
  straight into `ast.Constant(value=[<_Stringifier>])` (an arbitrary
  object ends up as the Constant value).
- When `deepcopy` asks that `_Stringifier` for `__deepcopy__`, the `_Stringifier` returns
  a new `_Stringifier` (an AST one level deeper) for any attribute, and when called it becomes an AST holding even the memo dict.
  Turning that into a string calls `ast.unparse` = `rewriting_unparse` again, which
  deep-copies an even larger tree: the recursion and the tree growth never stop.
- With `from __future__ import annotations` the annotation is a string from the start and does not pass through
  `_Stringifier`, so this does not happen.

The fix: the only things to map are the AST node fields (name strings); `ast.Constant` values are left alone
(Hy itself also "does not map string constants"). So only the nodes need to be copied.
Put the Constant value into the `copy.deepcopy` memo as itself beforehand, so the node is copied
without copying the value. The transform (keyword mincing) and the final call to the real `ast.unparse` are the same as in Hy.

Warning: this is a doeff-hy-side patch over a defect in Hy itself. The root causes are that Hy replaces `ast.unparse` process-wide
(which also drags in other users such as `annotationlib`) and that the replacement function
deep-copies arbitrary values. Text of the upstream report = `packages/doeff-hy/docs/hy-upstream-rewriting-unparse.md`.
On a Hy version where this is fixed upstream, `hy.compat.rewriting_unparse` is missing or `ast.unparse` is something else,
so this does nothing (the condition in `install`).
"""

import ast
import copy
import keyword
from collections.abc import Callable

_KEEP = ("True", "False", "None")


_MINCED_A = 0x1D41A  # MATHEMATICAL BOLD SMALL A (same mapping target as Hy)


def _mince(name: str) -> str:
    return chr(ord(name[0]) - ord("a") + _MINCED_A) + name[1:]


def unparse_without_copying_constants(
    true_unparse: Callable[[ast.AST], str],
) -> Callable[[ast.AST], str]:
    """Build a function with the same transform as Hy's `rewriting_unparse` that does not copy Constant values."""

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
    """Replace it if Hy's replacement is installed. Returns True if replaced. Safe to call repeatedly."""
    import hy.compat

    rewriting = hy.compat.__dict__.get("rewriting_unparse")
    true_unparse = hy.compat.__dict__.get("true_unparse")
    if rewriting is None or true_unparse is None or ast.unparse is not rewriting:
        return False
    replacement = unparse_without_copying_constants(true_unparse)
    ast.unparse = replacement
    hy.compat.rewriting_unparse = replacement
    return True
