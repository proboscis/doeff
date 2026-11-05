"""Failure cases for the types of doeff_hy._wire (wire.pyi) (child of #2279).

wire.hy is a Hy module, and without a type declaration (wire.pyi) pyright reads `parse` and `Malformed` as Unknown, so errors pile up
where the result of parse is used, in a form the author cannot fix. With the declaration:

- `row` in `(<- row (parse Row raw))` is `Row | Malformed`. After narrowing with Malformed, field reads pass as typed,
  and a type mix-up (adding a str field as an int) is an error.
- No reportUnknown* appears on the imported wire names (parse, Malformed).
"""

import contextlib
import dataclasses
import importlib
import inspect
import io
import json
import shutil
import sys
import typing
from dataclasses import dataclass
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # enable the Hy import hook
from doeff_hy._wire import WireShape

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")

MODULE = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defwire])
(import doeff_hy._wire [Malformed parse])

(defwire Row
  "A specimen row."
  {:names :camel :unknown :ignore}
  (#^ str name)
  (#^ int count))

(defk name-of [raw]
  {:pre [(: raw str)] :post [(: % str)]}
  (<- row (parse Row raw))
  (if (isinstance row Malformed)
      "malformed"
      row.name))

(defk wrong-sum [raw]
  {:pre [(: raw str)] :post [(: % int)]}
  (<- row (parse Row raw))
  (if (isinstance row Malformed)
      0
      (+ row.name 1)))
"""


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


def _check(root: Path) -> Run:
    from doeff_hy._static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    return tmp_path


@needs_pyright
def test_wire_names_are_not_unknown(probe: Path) -> None:
    # No "type unknown" errors on the imported parse and Malformed, or on row, the result of parse (Unknown without wire.pyi).
    unknown = [e for e in _check(probe).errors() if e[0].startswith("reportUnknown")]
    assert not [e for e in unknown if any(n in e[2] for n in ('"parse"', '"Malformed"', '"row"'))], unknown


@needs_pyright
def test_a_parsed_field_used_as_the_wrong_type_is_red(probe: Path) -> None:
    errors = _check(probe).errors()
    # The correct use (returning a str field as str after narrowing with Malformed, line 16) is not an error.
    assert not [e for e in errors if e[1] == 16], errors
    # Adding 1 to the str field name (line 23) is an error from the type mix-up.
    assert [e for e in errors if e[1] == 23 and e[0] == "reportOperatorIssue"], errors


# A specimen that passes defwire values to dump. The argument of dump / dump-json is WireValue (the Protocol of `__doeff_wire__: ClassVar[WireShape]`).
# The defwire expansion sets it with setattr and the type-check expansion drops that bookkeeping, so without a ClassVar annotation in the type body
# the defwire type is read as not satisfying WireValue. A plain defrecord value (with no wire shape) stays an error (the stub is not loosened).
DUMP_MODULE = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(import doeff_hy._json_value [JsonValue])
(import doeff_hy._wire [dump dump-json])

(defwire Status
  "A specimen state."
  {:names :camel}
  (#^ str phase))

(defwire Run
  "A specimen row (fields with :tags, :check, and defaults)."
  {:tags {:context "probe" :role "type"}
   :names :snake
   :check [(> (len run-id) 0)]}
  (#^ str run-id)
  (#^ Status status)
  (setv #^ int tries 0))

(defrecord Plain
  "A type with no wire shape."
  {}
  (#^ str phase))

(defk status-json [phase]
  {:pre [(: phase str)] :post [(: % JsonValue)]}
  (<- out (dump (Status :phase phase)))
  out)

(defk run-text [run]
  {:pre [(: run Run)] :post [(: % str)]}
  (<- out (dump-json run))
  out)

(defk plain-json [phase]
  {:pre [(: phase str)] :post [(: % JsonValue)]}
  (<- out (dump (Plain :phase phase)))
  out)
"""


@pytest.fixture
def dump_probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(DUMP_MODULE, encoding="utf-8")
    return tmp_path


def _line_of(text: str, marker: str) -> int:
    """Line number (from 1) of the line containing the marker in the specimen."""
    return next(n for n, line in enumerate(text.splitlines(), start=1) if marker in line)


@needs_pyright
def test_a_defwire_value_satisfies_wire_value(dump_probe: Path) -> None:
    errors = _check(dump_probe).errors()
    # No error where defwire values are passed to dump and dump-json (they satisfy WireValue).
    passed = (_line_of(DUMP_MODULE, "(dump (Status"), _line_of(DUMP_MODULE, "(dump-json run)"))
    assert not [e for e in errors if e[1] in passed], errors
    # A plain defrecord value passed to dump stays an error for not satisfying WireValue (the stub is not loosened).
    plain = _line_of(DUMP_MODULE, "(dump (Plain")
    assert [e for e in errors if e[1] == plain and e[0] == "reportArgumentType" and "WireValue" in e[2]], errors
    # Annotations in the type body are not read as fields (the constructor does not require a __doeff_wire__ argument). Even with two defwires side by side, the expansion's import
    # does not produce a duplicate error.
    assert not [e for e in errors if "__doeff_wire__" in e[2] or e[0] == "reportDuplicateImport"], errors


def test_the_wire_annotation_is_not_a_field() -> None:
    # A ClassVar annotation in the type body does not become a field: the dataclass fields, __match_args__, constructor arguments, and pydantic fields (wire JSON) are
    # only the fields written. The runtime __doeff_wire__ is the WireShape that setattr placed.
    tests_dir = str(Path(__file__).resolve().parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    row = importlib.import_module("defwire_deftests").LandingRow
    names = ("lane_id", "state", "landed_at", "ratio", "notes")
    assert tuple(f.name for f in dataclasses.fields(row)) == names
    assert row.__match_args__ == ()  # a kw_only dataclass has no positional fields
    assert tuple(inspect.signature(row).parameters) == names
    assert isinstance(row.__doeff_wire__, WireShape)
    assert typing.get_origin(row.__annotations__["__doeff_wire__"]) is typing.ClassVar
    assert tuple(row.__doeff_wire__.codec.schema()["properties"]) == (
        "laneId",
        "state",
        "landedAt",
        "ratio",
        "notes",
    )
