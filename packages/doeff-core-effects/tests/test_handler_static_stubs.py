"""Failure cases of the type declarations (memory_meter.pyi, memory_latest.pyi, os_file.pyi,
meter_effects.pyi) of the handlers for meters, latest values, and the file system, and of the meter effects.

The four modules are Hy, so without type declarations pyright reads the imported handlers and the meter effects and settings all as Unknown,
and a user that lines up the handlers gets a string of reportUnknown* errors in strict mode that the writer cannot fix.

- Failure case: run the same small .hy through doeff-hy-check --strict in two ways, a copy with the four declarations removed and a copy with them in place.
  With them removed, the imported names, the answer of a Program wrapped with a handler, and the cross-section of the meter show red as Unknown; with them in place it disappears. On the side with them in place,
  a type mix-up (adding a string to an int answer) shows red (the declaration carries the answer type of the handler from its body).
- That the names and arguments of the declarations match the implementation is checked by test_hy_module_stubs.py.
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "doeff_core_effects"

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

#: The declarations this test looks at (not the copy with them removed).
STUBS: tuple[str, ...] = ("memory_meter.pyi", "memory_latest.pyi", "os_file.pyi", "meter_effects.pyi")

#: The name of the copied package (a different name from the installed doeff_core_effects -- _copy_renamed).
COPY_NAME = "copied_core_effects"

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import copied_core_effects.meter_effects [MeterSettings ReadMeter])
(import copied_core_effects.memory_meter [memory-meter-handler])
(import copied_core_effects.memory_latest [memory-latest-handler])
(import copied_core_effects.os_file [os-file-handler])

(val SETTINGS (MeterSettings))

(defk counter-count []
  {:pre [] :post [(: % int)]}
  (<- snapshot (ReadMeter))
  (len snapshot.counters))

(defk handled-count []
  {:pre [] :post [(: % int)]}
  (<- counted (os-file-handler (memory-latest-handler ((memory-meter-handler SETTINGS) (counter-count)))))
  counted)

(defk wrong-count []
  {:pre [] :post [(: % int)]}
  (<- counted (os-file-handler (memory-latest-handler ((memory-meter-handler SETTINGS) (counter-count)))))
  (+ counted "x"))
"""

#: Names that become Unknown when the declarations are removed (names on import lines, variables receiving an answer).
UNKNOWN_NAMES: tuple[str, ...] = (
    '"MeterSettings"',
    '"ReadMeter"',
    '"memory_meter_handler"',
    '"memory_latest_handler"',
    '"os_file_handler"',
    '"snapshot"',
    '"counted"',
)


@dataclass(frozen=True)
class Run:
    """The result of running doeff-hy-check once (diagnostics as JSON)."""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]

    def unknown(self) -> list[tuple[str, int, str]]:
        return [e for e in self.errors() if e[0].startswith("reportUnknown")]


def _line_of(marker: str) -> int:
    """The line number (from 1) of the line containing marker in the specimen."""
    return next(n for n, line in enumerate(MODULE.splitlines(), start=1) if marker in line)


def _copy_renamed(target: Path, *, with_stubs: bool) -> None:
    """Copy doeff_core_effects to target as a package under another name (COPY_NAME) (remove STUBS unless with_stubs).

    If copied under the same name, the installed doeff_core_effects is also an ordinary package with __init__.py, so pyright resolves a module it cannot resolve in the copy
    (the .hy with the declarations removed) with the declarations on the installed side, and reads the declarations that should have been removed. Under another name it resolves within the copy alone.
    """
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns("__pycache__"))
    if not with_stubs:
        for name in STUBS:
            (target / name).unlink()
    for path in target.rglob("*"):
        if path.suffix in {".py", ".pyi", ".hy"}:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("doeff_core_effects", COPY_NAME), encoding="utf-8")


def _check(tmp_path: Path, *, with_stubs: bool) -> Run:
    """Place the copy (_copy_renamed) at the import root and run doeff-hy-check --strict on the specimen."""
    from doeff_hy._static_check import main

    copy = tmp_path / ("stubbed" if with_stubs else "bare") / COPY_NAME
    _copy_renamed(copy, with_stubs=with_stubs)
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
def test_without_the_stubs_the_handlers_are_unknown(tmp_path: Path) -> None:
    unknown = _check(tmp_path, with_stubs=False).unknown()
    missing = [name for name in UNKNOWN_NAMES if not any(name in e[2] for e in unknown)]
    assert missing == [], unknown


@needs_pyright
def test_with_the_stubs_nothing_is_unknown_and_a_wrong_type_is_red(tmp_path: Path) -> None:
    run = _check(tmp_path, with_stubs=True)
    # The correct usage (from the head of the specimen to just before wrong-count) has no red at all -- neither Unknown nor a type mix-up.
    right = range(1, _line_of("(defk wrong-count"))
    assert [e for e in run.errors() if e[1] in right] == [], run.errors()
    # Adding a string to an int answer is red (the declaration carries the type int of the answer of a Program wrapped with a handler from its body).
    wrong = _line_of('(+ counted "x")')
    assert [e for e in run.errors() if e[1] == wrong and e[0] == "reportOperatorIssue"], run.errors()
