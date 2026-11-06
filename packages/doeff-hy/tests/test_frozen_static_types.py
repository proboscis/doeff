"""Check on the types of doeff_hy._frozen (frozen.pyi) (#2245).

frozen.hy is a Hy module, and without a type declaration (frozen.pyi) pyright reads `FrozenMap` and the freezing functions as Unknown,
so reportUnknown* errors the author cannot fix pile up on definitions that return FrozenMap and on reads of FrozenMap values (about 1,400 of the same kind in agora's
baseline).

- Failure case: run the same small .hy through doeff-hy-check --strict in two copies, one with only frozen.hy and one with frozen.pyi also placed
  (the copies are packages with a different name; with the doeff_hy name, pyright would read from the doeff_hy that has the frozen.pyi missing from the copy). Without it, FrozenMap and the results are Unknown errors; with it they go away. On the side with it, a type mix-up of values (adding a string to an int value) is an error
  (FrozenMap carries the value type). A bare `FrozenMap` without type arguments is FrozenMap[object] and does not
  produce an error for forgetting the type arguments.
- The result of freeze-json-text (JSON string -> FrozenJson) can be narrowed to FrozenMap, and the contents of the narrowed value can be passed as JSON values (
  the point where the Any from json.loads is fixed to a type. Removing it from the stub makes that line of the specimen an error).
- Agreement: the names the stub declares exist in the runtime module, and function argument names match the implementation. FrozenMap is, as in the implementation,
  `Mapping[str, V]` (one type argument, the value type).
"""

import ast
import contextlib
import inspect
import io
import json
import shutil
import sys
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # enable the Hy import hook
from doeff_hy import _frozen as frozen

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")

PACKAGE = Path(frozen.__file__).parent

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import collections.abc [Mapping])
(import frozen_copy.frozen [FrozenMap frozen-map-of freeze-json frozen-json-object freeze-json-text])

(val SHALLOW (frozen-map-of {"a" 1} "probe"))

(defk labels []
  {:pre [] :post [(: % FrozenMap)]}
  (FrozenMap {"a" "x"}))

(defk label-count [m]
  {:pre [(: m FrozenMap)] :post [(: % int)]}
  (len (lfor key m key)))

(defk first-label []
  {:pre [] :post [(: % str)]}
  (<- m (labels))
  (str (get m "a")))

(defk shallow-sum []
  {:pre [] :post [(: % int)]}
  (+ (get SHALLOW "a") 1))

(defk frozen-of [text]
  {:pre [(: text str)] :post [(: % str)]}
  (str (freeze-json text)))

(defk takes-json [v]
  {:pre [(: v (| str int float bool None (get Mapping #(str object)) (get tuple #(object ...))))] :post [(: % None)]}
  None)

(defk frozen-feeds-json []
  {:pre [] :post [(: % None)]}
  (<- (takes-json (freeze-json {"a" [1 2]})))
  (<- (takes-json (get (frozen-json-object {"b" "x"} "probe") "b"))))

(defk text-feeds-json [text]
  {:pre [(: text str)] :post [(: % None)]}
  (val parsed (freeze-json-text text))
  (when (isinstance parsed FrozenMap)
    (<- (takes-json (get parsed "a"))))
  None)

(defk wrong-sum []
  {:pre [] :post [(: % int)]}
  (+ (get SHALLOW "a") "x"))
"""

#: Names that become Unknown when frozen.pyi is removed.
UNKNOWN_NAMES: tuple[str, ...] = ('"FrozenMap"', '"frozen_map_of"', '"SHALLOW"', '"m"')


@dataclass(frozen=True)
class Run:
    """Result of running doeff-hy-check once (JSON diagnostics)."""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _line_of(marker: str) -> int:
    """Line number (from 1) of the line containing the marker in the specimen."""
    return next(n for n, line in enumerate(MODULE.splitlines(), start=1) if marker in line)


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_stub: bool) -> Run:
    """Place a copy of frozen.hy (and frozen.pyi if with_stub) as package frozen_copy at the import root, and run the specimen through doeff-hy-check --strict.
    The copy's dir is also placed on sys.path -- doeff-hy-check tells an import target from a module whose "types are invisible because it is .hy" by the .hy on
    sys.path, so this matches how agora treats doeff's .hy modules."""
    from doeff_hy._static_check import main

    copy = tmp_path / ("stubbed" if with_stub else "bare") / "frozen_copy"
    copy.mkdir(parents=True)
    (copy / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(PACKAGE / "_frozen.hy", copy / "frozen.hy")
    if with_stub:
        shutil.copy(PACKAGE / "_frozen.pyi", copy / "frozen.pyi")
    monkeypatch.setattr(sys, "path", [str(copy.parent), *sys.path])
    root = tmp_path / "root"
    root.mkdir()
    (root / "probe.hy").write_text(MODULE, encoding="utf-8")
    (root / "pyrightconfig.json").write_text(json.dumps({"extraPaths": [str(copy.parent)]}), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_without_the_stub_frozen_map_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unknown = [e for e in _check(tmp_path, monkeypatch, with_stub=False).errors() if e[0].startswith("reportUnknown")]
    missing = [name for name in UNKNOWN_NAMES if not any(name in e[2] for e in unknown)]
    assert missing == [], unknown


@needs_pyright
def test_with_the_stub_nothing_is_unknown_and_a_wrong_value_type_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = _check(tmp_path, monkeypatch, with_stub=True).errors()
    # The correct use (from the head of the specimen to before wrong-sum) has no errors -- neither Unknown nor a forgotten type argument on a bare FrozenMap.
    right = range(1, _line_of("(defk wrong-sum"))
    assert [e for e in errors if e[1] in right] == [], errors
    # frozen-map-of carries the mapping value type: adding a string to an int value is an error.
    wrong = _line_of('(+ (get SHALLOW "a") "x")')
    assert [e for e in errors if e[1] == wrong and e[0] == "reportOperatorIssue"], errors


def test_the_stub_matches_frozen_hy() -> None:
    stub = ast.parse(PACKAGE.joinpath("_frozen.pyi").read_text(encoding="utf-8"))
    classes = [node.name for node in stub.body if isinstance(node, ast.ClassDef)]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    assert [name for name in [*classes, *(f.name for f in functions)] if not hasattr(frozen, name)] == []
    for function in functions:
        declared = [a.arg for a in function.args.args]
        assert declared == list(inspect.signature(getattr(frozen, function.name)).parameters), function.name
    # The implementation's FrozenMap is Mapping[str, V] (keys are strings, one type argument for the value type) -- the same shape as the stub's base.
    # types.get_original_bases exists from Python 3.12 -- on 3.11, read the same thing (the generic base as written) from the class's __orig_bases__.
    (base,) = (
        types.get_original_bases(frozen.FrozenMap)
        if sys.version_info >= (3, 12)
        else frozen.FrozenMap.__orig_bases__  # type: ignore[attr-defined] — not in the 3.11 type declarations; an attribute of the generic class
    )
    assert typing.get_origin(base) is Mapping
    key, value = typing.get_args(base)
    assert key is str and isinstance(value, typing.TypeVar)
    (frozen_map,) = [node for node in stub.body if isinstance(node, ast.ClassDef) and node.name == "FrozenMap"]
    assert ast.unparse(frozen_map.bases[0]) == "Mapping[str, _V]"
