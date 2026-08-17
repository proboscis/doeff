"""Counterexamples for ADR-DOE-ENFORCE-001 R6 (load-scaled test deadlines).

A wall-clock deadline exists to catch a HANG.  On an oversubscribed machine
it stops measuring "hung" and starts measuring "busy", and the gate turns red
on correct work — measured 2026-08-17: five consecutive land-gate failures on
this machine (18 cores, load 60-156), each on a different subprocess-spawning
test, all reproducing on pristine main.

The tests below pin the three properties that make the deadline mean "hang"
again, and the one that keeps the two deadlines from crossing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _root_conftest() -> Any:
    """Import the ROOT conftest as a module (it is not importable by name)."""
    spec = importlib.util.spec_from_file_location(
        "doeff_root_conftest", REPO_ROOT / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deadline_scales_with_oversubscription(monkeypatch: pytest.MonkeyPatch) -> None:
    """The factor is load-per-core, floored at 1 and capped.

    Counterexample: a constant deadline.  At load 100 on 18 cores every
    subprocess-spawning test takes ~5.5x longer while the budget stays 60 s,
    so the gate reports "timeout" for work that would pass on an idle machine.
    """
    conftest = _root_conftest()

    monkeypatch.setattr(conftest.os, "cpu_count", lambda: 10)
    monkeypatch.setattr(conftest.os, "getloadavg", lambda: (50.0, 0.0, 0.0))
    assert conftest.deadline_scale() == pytest.approx(5.0)

    # An idle machine never SHRINKS the deadline — the floor is 1.
    monkeypatch.setattr(conftest.os, "getloadavg", lambda: (0.2, 0.0, 0.0))
    assert conftest.deadline_scale() == pytest.approx(1.0)


def test_scale_is_capped_so_a_real_hang_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counterexample: an uncapped factor.

    Scaling without a ceiling turns "catch a hang" into "wait forever on a
    thrashing machine" — the run never ends and nobody learns anything.
    """
    conftest = _root_conftest()

    monkeypatch.setattr(conftest.os, "cpu_count", lambda: 10)
    monkeypatch.setattr(conftest.os, "getloadavg", lambda: (10_000.0, 0.0, 0.0))
    assert conftest.deadline_scale() == pytest.approx(conftest._DEADLINE_SCALE_CAP)


def test_scaling_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI controls its own load, so it must be able to hold the deadline fixed."""
    conftest = _root_conftest()

    monkeypatch.setattr(conftest.os, "cpu_count", lambda: 10)
    monkeypatch.setattr(conftest.os, "getloadavg", lambda: (50.0, 0.0, 0.0))
    monkeypatch.setenv("PYTEST_DEADLINE_SCALE", "off")
    assert conftest.deadline_scale() == pytest.approx(1.0)


def test_watchdog_never_fires_before_the_per_test_deadline() -> None:
    """Counterexample: scaling one deadline and not the other.

    The watchdog is SIGKILL — it does not fail a test, it destroys the run and
    every result in it.  If the per-test deadline is raised past the watchdog,
    a merely-slow test stops producing one red test and starts producing a
    dead process (observed 2026-08-17: PYTEST_TIMEOUT=600 against the unscaled
    90 s watchdog killed the battery at 45%).  The watchdog must therefore
    always sit above the per-test deadline, at every scale.
    """
    conftest = _root_conftest()

    for scale in (1.0, 2.0, 5.5, 8.0):
        for per_test in (1.0, 60.0, 600.0):
            watchdog = conftest.scaled_watchdog_timeout(90.0, per_test, scale)
            assert watchdog > per_test * scale, (
                f"watchdog {watchdog}s would SIGKILL before the "
                f"{per_test * scale}s per-test deadline (scale x{scale})"
            )

    # ...and it still grows with the machine, so it is not just a big constant.
    assert conftest.scaled_watchdog_timeout(
        90.0, 60.0, 4.0
    ) > conftest.scaled_watchdog_timeout(90.0, 60.0, 1.0)


def test_explicit_caller_deadline_is_scaled_not_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An env var or --timeout from the caller is the number they meant.

    Counterexample: reading only the ini.  A caller who raised the deadline on
    purpose would silently get 60 s back, which makes the escape hatch a lie.
    """
    conftest = _root_conftest()

    class _Config:
        class option:  # noqa: N801 - mimics pytest's Config.option namespace
            timeout = None

        @staticmethod
        def getini(name: str) -> str:
            assert name == "timeout"
            return "60"

    monkeypatch.setenv("PYTEST_TIMEOUT", "300")
    assert conftest._per_test_base_seconds(_Config) == pytest.approx(300.0)

    monkeypatch.delenv("PYTEST_TIMEOUT")
    _Config.option.timeout = 120
    assert conftest._per_test_base_seconds(_Config) == pytest.approx(120.0)

    _Config.option.timeout = None
    assert conftest._per_test_base_seconds(_Config) == pytest.approx(60.0)
