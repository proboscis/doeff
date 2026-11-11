# Report text for Hy upstream (not yet submitted)

doeff-hy works around this in `doeff_hy/_ast_unparse.py`. The root cause is in Hy itself, so the text below should be
filed as an issue on hylang/hy (whether to submit is still open. As of 2026-09-23 no identical report was found, and
`hy/compat.py` on master has the same shape). Once upstream ships a fixed version, `install()` does nothing
(`ast.unparse` is no longer `hy.compat.rewriting_unparse`), at which point the workaround should be removed.

---

**Title:** `hy.compat.rewriting_unparse` recurses without bound (memory grows to GBs) when Python 3.14 `annotationlib` stringifies annotations

**Environment:** Hy 1.3.0 (same code on master), CPython 3.14.3 (also free-threaded build), pytest 9.1.1

**Summary**

Importing `hy` replaces `ast.unparse` process-wide with `hy.compat.rewriting_unparse`, which
`copy.deepcopy`s the whole tree before keyword mincing. On Python 3.14, `annotationlib`
(used by `inspect.signature(..., annotation_format=Format.STRING)`, which pytest 9 calls for
every fixture) builds ASTs whose `ast.Constant.value` holds arbitrary objects, including
`annotationlib._Stringifier` instances. Deep-copying a `_Stringifier` asks it for
`__deepcopy__`; `_Stringifier.__getattr__` answers every attribute with a new `_Stringifier`,
calling it wraps the memo dict into a bigger AST, and stringifying that calls `ast.unparse`
(= `rewriting_unparse`) again on a larger tree. The process never finishes and its memory
grows at ~150 MB/s (we saw 10 GB).

**Reproduction**

```python
import inspect
import hy  # installs hy.compat.rewriting_unparse
from annotationlib import Format
from collections.abc import Callable

class Widget: ...

def f(x: Callable[[int], Widget]): ...

inspect.signature(f, annotation_format=Format.STRING)  # never returns; memory keeps growing
```

The same happens with pytest when a `conftest.py` without `from __future__ import annotations`
declares a fixture annotated like `-> Callable[[Program], object]` and `hy` has been imported
(e.g. by a plugin). Without `import hy` the call returns `(x: 'Callable[[int], Widget]')`
immediately.

**Why**

- In `Format.STRING`, `annotationlib._Stringifier.__convert_to_ast` returns
  `ast.Constant(value=other)` for any non-stringifier value, so the list `[int]` inside the
  subscript becomes `Constant(value=[<_Stringifier int>])`.
- `rewriting_unparse` deep-copies the tree, including `Constant.value`.

**Suggested fix**

Keyword mincing only touches string fields of non-`Constant` nodes, so constant values never
need to be copied. Seeding the deepcopy memo with the constant values keeps the behaviour and
avoids copying arbitrary objects:

```python
def rewriting_unparse(ast_obj):
    constants = {id(n.value): n.value for n in ast.walk(ast_obj) if isinstance(n, ast.Constant)}
    ast_obj = copy.deepcopy(ast_obj, constants)
    ...  # unchanged mincing loop
    return true_unparse(ast_obj)
```

A broader option is to stop replacing `ast.unparse` globally and call the mincing unparser only
from `hy2py` / Hy's own code paths, so other users of `ast.unparse` (annotationlib, pytest,
typing tools) are unaffected.
