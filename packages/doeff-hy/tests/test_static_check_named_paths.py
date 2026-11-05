"""A path named to doeff-hy-check must not be silently dropped when it differs from how the root is written (an absolute path that does not follow symlinks).

Failure case (2026-10-11, 22 type reds in agora-controllers): the worktree root `/home/ubuntu/.worktrees/<tree>` is a symlink to `/work/toolset/worktrees/<tree>`;
`--root "$PWD"` uses the symlink spelling, while `Path.cwd()`, which joins relative paths, uses the resolved spelling. The named file was treated as "outside the root" and
silently dropped, so a file with 22 reds reported 0 (passing an absolute path gave 22).

- A relative path and an absolute path give the same findings for the same file (symlink spelling differences are mapped into the root).
- Naming a path truly outside the root is rejected explicitly (exit 2) instead of silently giving 0 findings.
"""

import contextlib
import io
import json
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")

#: A sample that always yields 1 red for rebinding and 2 setv warnings (same shape as BINDINGS in test_static_check.py).
BINDINGS = """(require doeff-hy.macros [defk <-])

(defk rebinds [x]
  {:pre [(: x int)] :post [(: % int)]}
  (setv y x)
  (setv y (+ y 1))
  (val z (* y 2))
  z)
"""


class _Checked(NamedTuple):
    """Result of running main once: exit code, findings (rule, severity, line), stderr."""

    code: int
    found: list[tuple[str, str, int]]
    err: str


def _checked(argv: list[str]) -> _Checked:
    """Run main once and return the exit code, findings (rule, severity, line) and stderr."""
    from doeff_hy._static_check import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    found = sorted((d["rule"], d["severity"], d["line"]) for d in json.loads(out.getvalue() or "[]"))
    return _Checked(code, found, err.getvalue())


@needs_pyright
def test_a_relative_path_under_a_symlinked_root_gives_the_same_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "bindings.hy").write_text(BINDINGS, encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(linked)  # os.getcwd() returns the resolved path (real) — the root is still passed with the symlink spelling (linked)
    absolute = _checked(["--root", str(linked), "--json", "--no-cache", str(linked / "bindings.hy")])
    relative = _checked(["--root", str(linked), "--json", "--no-cache", "bindings.hy"])
    assert ("doeff-hy-rebind", "error", 6) in absolute.found, absolute
    assert (relative.code, relative.found) == (absolute.code, absolute.found), (relative, absolute)


def test_a_named_path_outside_the_root_is_refused_not_dropped(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.hy"
    outside.write_text(BINDINGS, encoding="utf-8")
    code, found, err = _checked(["--root", str(root), "--json", "--no-cache", str(outside)])
    assert code == 2 and found == [], (code, found, err)
    assert str(outside) in err and "outside the root" in err, err
