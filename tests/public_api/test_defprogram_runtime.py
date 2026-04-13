"""Reproducer: defprogram produces wrong type instead of DoExpr.

After Dict.append fix (5dca420a), defprogram compiles but:
- Pure body (no effects): returns the raw value (e.g. int) instead of DoExpr
- Effectful body (with <-): returns a generator instead of DoExpr

defprogram must always produce a DoExpr (Program) that can be passed to doeff.run().
"""

from __future__ import annotations

import subprocess
import sys
import tempfile

import pytest

from doeff import DoExpr, do, run


def _run_hy_file(code: str) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile(suffix=".hy", mode="w", delete=False) as f:
        f.write(code)
        f.flush()
        return subprocess.run(
            [sys.executable, "-c",
             f"import hy; import runpy; runpy.run_path({f.name!r})"],
            capture_output=True,
            text=True,
            timeout=30,
        )


class TestDefprogramProducesDoExpr:
    def test_pure_body_is_doexpr(self) -> None:
        """defprogram with pure body (no effects) must be DoExpr, not raw value."""
        result = _run_hy_file(
            '(require doeff-hy.macros [defprogram])\n'
            '(import doeff [do :as _doeff-do DoExpr run])\n'
            '\n'
            '(defprogram p-pure\n'
            '  {:post []}\n'
            '  42)\n'
            '\n'
            '(assert (isinstance p-pure DoExpr)\n'
            '  (+ "Expected DoExpr, got " (. (type p-pure) __name__)))\n'
            '(assert (= (run p-pure) 42))\n'
            '(print "OK")\n'
        )
        assert result.returncode == 0, (
            f"Pure defprogram failed:\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout

    def test_effectful_body_is_doexpr(self) -> None:
        """defprogram with effect binding (<-) must be DoExpr, not generator."""
        result = _run_hy_file(
            '(require doeff-hy.macros [defprogram <-])\n'
            '(import doeff [do :as _doeff-do DoExpr run WithHandler Ask])\n'
            '(import doeff_core_effects [reader])\n'
            '\n'
            '(defprogram p-effectful\n'
            '  {:post []}\n'
            '  (<- x (Ask "val"))\n'
            '  (+ x 1))\n'
            '\n'
            '(assert (isinstance p-effectful DoExpr)\n'
            '  (+ "Expected DoExpr, got " (. (type p-effectful) __name__)))\n'
            '(setv result (run (WithHandler (reader :env {"val" 41}) p-effectful)))\n'
            '(assert (= result 42)\n'
            '  (+ "Expected 42, got " (repr result)))\n'
            '(print "OK")\n'
        )
        assert result.returncode == 0, (
            f"Effectful defprogram failed:\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout
