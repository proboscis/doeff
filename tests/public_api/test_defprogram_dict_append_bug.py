"""Reproducer for defprogram Dict.append bug.

defprogram / defp with {:post []} crashes at macro expansion time with:
  AttributeError: 'Dict' object has no attribute 'append'

Regression in _build_defp (c8010400) where hy.models.Dict construction
uses .append instead of passing items to the constructor.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def _run_hy_code(code: str) -> subprocess.CompletedProcess:
    """Write Hy code to a temp file and run it."""
    with tempfile.NamedTemporaryFile(suffix=".hy", mode="w", delete=False) as f:
        f.write(code)
        f.flush()
        return subprocess.run(
            [sys.executable, "-c", f"import hy; import runpy; runpy.run_path({f.name!r})"],
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestDefprogramDictAppendBug:
    def test_defprogram_with_empty_post_compiles(self) -> None:
        """defprogram with {:post []} should compile and run without error."""
        result = _run_hy_code(
            '(require doeff-hy.macros [defprogram <-])\n'
            '(import doeff [do :as _doeff-do])\n'
            '(defprogram p-test\n'
            '  {:post []}\n'
            '  42)\n'
            '(print "OK" p-test)\n'
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "OK" in result.stdout

    def test_defp_with_empty_post_compiles(self) -> None:
        """defp with {:post []} should compile and run without error."""
        result = _run_hy_code(
            '(require doeff-hy.macros [defp <-])\n'
            '(import doeff [do :as _doeff-do])\n'
            '(defp p-test\n'
            '  {:post []}\n'
            '  42)\n'
            '(print "OK" p-test)\n'
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "OK" in result.stdout

    def test_defprogram_with_type_post_compiles(self) -> None:
        """defprogram with {:post [(: % int)]} should compile and run."""
        result = _run_hy_code(
            '(require doeff-hy.macros [defprogram <-])\n'
            '(import doeff [do :as _doeff-do])\n'
            '(defprogram p-typed\n'
            '  {:post [(: % int)]}\n'
            '  42)\n'
            '(print "OK" p-typed)\n'
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "OK" in result.stdout
