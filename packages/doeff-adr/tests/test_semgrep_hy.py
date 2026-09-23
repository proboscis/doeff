"""Semgrep over Hy through macro expansion (doeff_adr.hy_expand / doeff_adr.semgrep_hy).

A regular expression over Hy text sees neither what a macro writes nor a name
brought in under an alias.  Expanding with Hy's own compiler and scanning the
Python sees both, and findings are reported at the Hy line.
"""

import importlib
import re
import sys
import textwrap
from pathlib import Path

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from doeff_adr.hy_expand import expand_hy_source
from doeff_adr.registry import clear_registry
from doeff_adr.semgrep_hy import HyScanExpansionError, scan_with_hy_expansion

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("semgrep") is None, reason="semgrep is required"
)

RULES = """\
rules:
  - id: no-direct-socket
    languages: [python]
    severity: ERROR
    message: sockets go through effects
    pattern: socket.socket(...)
  - id: no-open
    languages: [python]
    severity: ERROR
    message: files go through effects
    pattern: open(...)
  - id: no-pathlib-read
    languages: [python]
    severity: ERROR
    message: files go through effects
    pattern: pathlib.Path(...).read_text(...)
    paths:
      include:
        - /app/lab/**
"""


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path: Path):
    (tmp_path / "rules.yaml").write_text(RULES, encoding="utf-8")
    yield tmp_path
    for name in [name for name in sys.modules if name.startswith("app")]:
        del sys.modules[name]
    importlib.invalidate_caches()


def _hits(findings) -> list[tuple[str, int, str]]:
    return [(f.path, f.line, f.short_rule_id) for f in findings]


def test_expanded_source_maps_back_to_hy_lines() -> None:
    expanded = expand_hy_source(
        "(import json)\n\n(defn load [p]\n  (json.loads\n    (open p)))\n",
        filename="m.hy",
        module_name="m",
    )
    open_line = next(
        number
        for number, text in enumerate(expanded.python_source.splitlines(), start=1)
        if "open(p)" in text
    )
    assert expanded.hy_line(open_line) == 5


def test_an_fstring_earlier_in_the_file_does_not_blur_later_lines() -> None:
    expanded = expand_hy_source(
        '(defn name [x] f"{x}.{x}.log")\n\n\n(defn load [p]\n  (open p))\n',
        filename="m.hy",
        module_name="m",
    )
    open_line = next(
        number
        for number, text in enumerate(expanded.python_source.splitlines(), start=1)
        if "open(p)" in text
    )
    assert expanded.hy_line(open_line) == 5


def test_aliased_imports_are_seen_through(tree: Path) -> None:
    # A regex over Hy text looks for `(import socket)` / `socket.`; neither appears.
    _write(tree, "app/lab/net.hy", """\
        (import socket :as s)
        (import pathlib [Path :as P])

        (defn dial [host]
          (s.socket))

        (defn slurp [p]
          (.read-text (P p)))
        """)

    findings = scan_with_hy_expansion(tree / "rules.yaml", tree, ["app"])

    assert _hits(findings) == [
        ("app/lab/net.hy", 5, "no-direct-socket"),
        ("app/lab/net.hy", 8, "no-pathlib-read"),
    ]


def test_project_macros_are_expanded_before_the_scan(tree: Path) -> None:
    # The file under scan never spells `open`; the project's macro writes it.
    _write(tree, "app/__init__.py", "")
    _write(tree, "app/macros.hy", """\
        (defmacro defsvc [name #* body]
          `(defn ~name [] (setv log (open "/tmp/svc.log" "a")) ~@body))
        """)
    _write(tree, "app/lab/svc.hy", """\
        (require app.macros [defsvc])

        (defsvc keeper
          42)
        """)

    findings = scan_with_hy_expansion(tree / "rules.yaml", tree, ["app/lab"])

    assert _hits(findings) == [("app/lab/svc.hy", 3, "no-open")]


def test_path_filters_apply_to_the_same_relative_paths(tree: Path) -> None:
    _write(tree, "app/other/slurp.hy", """\
        (import pathlib)
        (defn slurp [p] (.read-text (pathlib.Path p)))
        """)

    assert scan_with_hy_expansion(tree / "rules.yaml", tree, ["app"]) == []


def test_python_files_are_scanned_as_they_are(tree: Path) -> None:
    _write(tree, "app/lab/plain.py", "import socket\n\n\ndef dial():\n    return socket.socket()\n")

    findings = scan_with_hy_expansion(tree / "rules.yaml", tree, ["app"])

    assert _hits(findings) == [("app/lab/plain.py", 5, "no-direct-socket")]


def test_a_file_that_does_not_expand_fails_the_scan(tree: Path) -> None:
    _write(tree, "app/lab/broken.hy", "(require app.no_such_macros [defsvc])\n(defsvc x 1)\n")

    with pytest.raises(HyScanExpansionError, match=re.escape("app/lab/broken.hy")):
        scan_with_hy_expansion(tree / "rules.yaml", tree, ["app"])


def test_installed_defsemgrep_expands_hy_fixtures(tmp_path: Path) -> None:
    (tmp_path / "rules.yaml").write_text(RULES, encoding="utf-8")
    adr = _write(tmp_path, "test_hy_fixture_adr.hy", """\
        (require doeff-adr.macros [defsemgrep])

        (defsemgrep aliased-socket-rule
          "no-direct-socket"
          [{"relative-path" "app/lab/net.hy"
            "source" "(import socket :as s)\\n(defn dial [] (s.socket))\\n"}]
          [{"relative-path" "app/lab/net.hy"
            "source" "(defn dial [] (socket-effect))\\n"}]
          :config "rules.yaml"
          :expand-hy True)
        """)
    sys.path.insert(0, str(tmp_path))
    clear_registry()
    try:
        module = importlib.import_module(adr.stem)
        module.test_aliased_socket_rule_defsemgrep()
    finally:
        clear_registry()
        sys.path.remove(str(tmp_path))
        sys.modules.pop(adr.stem, None)
